"""vLLM 동시 요청 부하테스트. 표준 라이브러리만 쓴다.

동시성 단계마다 워커 N개가 응답을 받는 즉시 다음 스트리밍 요청을 보낸다(closed model).
설정은 환경변수(02_cronjob.yaml), 결과는 RESULT_DIR/<JOB_NAME>/ 에 남는다.
  python bench.py selftest   # 파싱·백분위 자체 점검
"""
import csv, http.client, json, os, re, sys, threading, time, urllib.parse, urllib.request

E = os.environ
VLLM_URL = E.get("VLLM_URL", "http://vllm-qwen.vllm.svc:8000").rstrip("/")
PROM_URL = E.get("PROM_URL", "http://kube-prometheus-stack-prometheus.monitoring.svc:9090").rstrip("/")
CONCURRENCY = [int(x) for x in E.get("CONCURRENCY", "1 2 4 8").split()]
REQUESTS_PER_STEP = int(E.get("REQUESTS_PER_STEP", "40"))
PROMPT_WORDS = int(E.get("PROMPT_WORDS", "200"))
MAX_TOKENS = int(E.get("MAX_TOKENS", "128"))
REQUEST_TIMEOUT = float(E.get("REQUEST_TIMEOUT", "300"))
WARMUP_REQUESTS = int(E.get("WARMUP_REQUESTS", "2"))
POLL_SECONDS = float(E.get("POLL_SECONDS", "5"))
COOLDOWN_SECONDS = float(E.get("COOLDOWN_SECONDS", "10"))
RUN = E.get("JOB_NAME") or time.strftime("local-%Y%m%d-%H%M%S")
OUT = os.path.join(E.get("RESULT_DIR", "./results"), RUN)
HOST = urllib.parse.urlsplit(VLLM_URL)

# 단어 하나가 대략 토큰 하나다. 실제 입력 토큰 수는 응답 usage로 기록한다.
WORDS = "apple river stone cloud green music table light".split()
BASE_PROMPT = "Continue this list of words: " + " ".join(WORDS[i % len(WORDS)] for i in range(PROMPT_WORDS))

# vLLM 버전에 따라 이름이 바뀐 지표는 앞의 이름부터 찾는다.
GAUGES = {
    "running": ["vllm:num_requests_running"],
    "waiting": ["vllm:num_requests_waiting"],
    "kv_usage": ["vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc"],
    "preemptions": ["vllm:num_preemptions_total", "vllm:num_preemptions"],
}
HISTS = {
    "queue_time": ["vllm:request_queue_time_seconds"],
    "ttft": ["vllm:time_to_first_token_seconds"],
    "itl": ["vllm:inter_token_latency_seconds", "vllm:time_per_output_token_seconds"],
    "e2e": ["vllm:e2e_request_latency_seconds"],
}
LINE = re.compile(r"^([a-zA-Z_:][\w:]*)(\{[^}]*\})?\s+(\S+)")


def parse_metrics(text):
    """지표 이름별로 라벨을 합산한 값."""
    out = {}
    for line in text.splitlines():
        m = LINE.match(line)
        if m:
            try:
                out[m[1]] = out.get(m[1], 0.0) + float(m[3])
            except ValueError:
                pass
    return out


def metric_labels(text, name):
    m = re.search("^" + re.escape(name) + r"\{([^}]*)\}", text, re.M)
    return dict(re.findall(r'(\w+)="([^"]*)"', m[1])) if m else {}


def pick(metrics, names, suffix=""):
    return next((metrics[n + suffix] for n in names if n + suffix in metrics), None)


def pct(values, p):
    v = sorted(values)
    return v[max(0, -(-len(v) * p // 100) - 1)] if v else ""


def get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode()


def vllm_metrics():
    return parse_metrics(get(VLLM_URL + "/metrics"))


def prom(query, at):
    try:
        q = urllib.parse.urlencode({"query": query, "time": at})
        result = json.loads(get(f"{PROM_URL}/api/v1/query?{q}"))["data"]["result"]
        return round(float(result[0]["value"][1]), 3) if result else ""
    except Exception as e:
        print(f"prometheus 조회 실패 ({query}): {e}", file=sys.stderr)
        return ""


def resources(start, end):
    # ponytail: 수집 간격(30s)보다 짧은 창은 값이 비어서 최소 60s로 잡는다. 단계가 60s보다 짧으면 쿨다운 구간이 섞인다.
    w = f"{max(int(end - start), 60)}s"
    cpu = 'sum(rate(container_cpu_usage_seconds_total{namespace="vllm",container="server"}[1m]))'
    mem = 'sum(container_memory_working_set_bytes{namespace="vllm",container="server"})'
    return {k: prom(q, end) for k, q in {
        "gpu_util_avg": f"avg(avg_over_time(DCGM_FI_DEV_GPU_UTIL[{w}]))",
        "gpu_util_max": f"max(max_over_time(DCGM_FI_DEV_GPU_UTIL[{w}]))",
        "vram_used_mib_max": f"max(max_over_time(DCGM_FI_DEV_FB_USED[{w}]))",
        "cpu_cores_avg": f"avg_over_time(({cpu})[{w}:15s])",
        "cpu_cores_max": f"max_over_time(({cpu})[{w}:15s])",
        "mem_mib_max": f"max_over_time(({mem})[{w}:15s]) / 1048576",
    }.items()}


def restarts(at):
    return prom('sum(kube_pod_container_status_restarts_total{namespace="vllm"})', at)


def one_request(model, conc, tag):
    """스트리밍 요청 1건. TTFT는 첫 생성 토큰이 도착한 시점이다."""
    body = json.dumps({
        "model": model,
        # 앞머리를 요청마다 바꿔 prefix cache가 적중하지 않게 한다.
        "messages": [{"role": "user", "content": f"[{tag}] {BASE_PROMPT}"}],
        "max_tokens": MAX_TOKENS, "temperature": 0, "ignore_eos": True,
        "stream": True, "stream_options": {"include_usage": True},
    })
    row = {"concurrency": conc, "start": round(time.time(), 3), "status": "ok",
           "ttft_s": "", "e2e_s": "", "tpot_s": "", "prompt_tokens": "", "output_tokens": ""}
    t0, first, usage = time.monotonic(), None, None
    conn = http.client.HTTPConnection(HOST.hostname, HOST.port or 80, timeout=REQUEST_TIMEOUT)
    try:
        conn.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
        resp = conn.getresponse()
        if resp.status != 200:
            resp.read()
            row["status"] = f"http_{resp.status}"
            return row
        for line in resp:
            if time.monotonic() - t0 > REQUEST_TIMEOUT:
                raise TimeoutError
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                break
            chunk = json.loads(data)
            usage = chunk.get("usage") or usage
            if first is None and any(v for c in chunk.get("choices", [])
                                     for k, v in c.get("delta", {}).items() if k != "role"):
                first = time.monotonic()
        end = time.monotonic()
        if first is None or not usage:
            row["status"] = "bad_response"
            return row
        out = usage.get("completion_tokens", 0)
        row.update(ttft_s=round(first - t0, 4), e2e_s=round(end - t0, 4),
                   prompt_tokens=usage.get("prompt_tokens", ""), output_tokens=out,
                   tpot_s=round((end - first) / (out - 1), 5) if out > 1 else "")
    except TimeoutError:
        row["status"] = "timeout"
    except (OSError, http.client.HTTPException):
        row["status"] = "connection_error"
    # 예외가 워커 스레드를 죽이면 단계가 끝나지 않는다. 응답 형식 문제는 모두 여기로 모은다.
    except Exception:
        row["status"] = "bad_response"
    finally:
        conn.close()
    return row


def run_step(model, conc, rows, warm, done):
    """동시성 N이 유지된 구간의 요청만 REQUESTS_PER_STEP건 기록한다.

    처음 완료되는 N건은 대기열이 차기 전에 출발한 요청이라 버리고 warm을 세운다.
    REQUESTS_PER_STEP건을 기록하면 done을 세운다. 그때 처리 중인 요청은 동시성이 줄어드는 중에 끝나므로 버린다.
    반환한 스레드는 버리는 요청까지 끝나면 종료된다.
    """
    issued, completed, lock = [0], [0], threading.Lock()

    def worker():
        while not done.is_set():
            with lock:
                issued[0] += 1
                n = issued[0]
            r = one_request(model, conc, f"{RUN}-c{conc}-{n}")
            with lock:
                if done.is_set():
                    return
                completed[0] += 1
                if completed[0] <= conc:
                    if completed[0] == conc:
                        warm.set()
                    continue
                rows.append(r)
                if len(rows) % 10 == 0:
                    print(f"동시성 {conc}: {len(rows)}/{REQUESTS_PER_STEP} 완료")
                if len(rows) >= REQUESTS_PER_STEP:
                    done.set()

    print(f"동시성 {conc} 시작")
    threads = [threading.Thread(target=worker) for _ in range(conc)]
    for t in threads:
        t.start()
    return threads


class Poller(threading.Thread):
    """vLLM /metrics를 POLL_SECONDS마다 읽어 현재 단계와 함께 기록한다."""

    def __init__(self):
        super().__init__(daemon=True)
        self.step, self.rows, self.stop = "", [], threading.Event()

    def run(self):
        while not self.stop.wait(POLL_SECONDS):
            try:
                m = vllm_metrics()
            except Exception as e:
                print(f"/metrics 수집 실패: {e}", file=sys.stderr)
                continue
            self.rows.append({"ts": round(time.time(), 3), "concurrency": self.step,
                              **{k: pick(m, names) for k, names in GAUGES.items()}})


def wait_idle():
    """실행·대기 요청이 0이 된 뒤 COOLDOWN_SECONDS를 더 쉰다."""
    deadline = time.monotonic() + REQUEST_TIMEOUT
    while time.monotonic() < deadline:
        m = vllm_metrics()
        busy = (pick(m, GAUGES["running"]) or 0) + (pick(m, GAUGES["waiting"]) or 0)
        if not busy:
            break
        print(f"대기열 비우는 중: 실행·대기 {busy:g}건")
        time.sleep(5)
    else:
        print("경고: vLLM 대기열이 비지 않았다. 다른 트래픽이 섞였을 수 있다.", file=sys.stderr)
    time.sleep(COOLDOWN_SECONDS)


def summarize(conc, rows, polls, before, after, start, end, res, r0, r1):
    ok = [r for r in rows if r["status"] == "ok"]
    dur = end - start
    col = lambda k: [r[k] for r in ok if r[k] != ""]
    poll = lambda k: [p[k] for p in polls if p[k] is not None]
    hist_mean = lambda names: (
        round((pick(after, names, "_sum") - pick(before, names, "_sum"))
              / (pick(after, names, "_count") - pick(before, names, "_count")), 4)
        if pick(after, names, "_count") and pick(after, names, "_count") > pick(before, names, "_count") else "")
    p0, p1 = pick(before, GAUGES["preemptions"]), pick(after, GAUGES["preemptions"])
    return {
        "concurrency": conc, "requests": len(rows), "ok": len(ok), "duration_s": round(dur, 1),
        "error_rate": round(sum(r["status"] not in ("ok", "timeout") for r in rows) / len(rows), 4),
        "timeout_rate": round(sum(r["status"] == "timeout" for r in rows) / len(rows), 4),
        "ok_rps": round(len(ok) / dur, 3),
        "output_tok_per_s": round(sum(col("output_tokens")) / dur, 1),
        **{f"{k}_p{p}": pct(col(k), p) for k in ("e2e_s", "ttft_s", "tpot_s") for p in (50, 95)},
        "max_running": max(poll("running"), default=""), "max_waiting": max(poll("waiting"), default=""),
        "server_queue_time_mean_s": hist_mean(HISTS["queue_time"]),
        "server_ttft_mean_s": hist_mean(HISTS["ttft"]),
        "server_itl_mean_s": hist_mean(HISTS["itl"]),
        "server_e2e_mean_s": hist_mean(HISTS["e2e"]),
        # vLLM은 0~1 비율로 내보낸다.
        "kv_usage_max": round(max(poll("kv_usage"), default=0), 4) if poll("kv_usage") else "",
        "preemptions_delta": p1 - p0 if p0 is not None and p1 is not None else "",
        **res,
        "restarts_delta": r1 - r0 if "" not in (r0, r1) else "",
    }


def write_csv(name, rows):
    if not rows:
        return
    with open(os.path.join(OUT, name), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main():
    os.makedirs(OUT, exist_ok=True)
    text = get(VLLM_URL + "/metrics")
    # 첫 실행 때 실제 지표 이름을 확인하는 원본이다.
    with open(os.path.join(OUT, "metrics_start.txt"), "w") as f:
        f.write(text)
    card = json.loads(get(VLLM_URL + "/v1/models"))["data"][0]
    cache = metric_labels(text, "vllm:cache_config_info")
    blocks, block_size = cache.get("num_gpu_blocks"), cache.get("block_size")
    meta = {
        "run": RUN, "model": card["id"], "max_model_len": card.get("max_model_len"),
        # run.sh가 vLLM 파드 인자에서 읽어 넘긴다.
        "max_num_seqs": E.get("MAX_NUM_SEQS", ""),
        "kv_cache_tokens": int(blocks) * int(block_size) if blocks and block_size else None,
        "cache_config": cache,
        "missing_metrics": [k for k, names in {**GAUGES, **HISTS}.items()
                            if pick(parse_metrics(text), names, "" if k in GAUGES else "_count") is None],
        "settings": {k: E.get(k) for k in ("CONCURRENCY", "REQUESTS_PER_STEP", "PROMPT_WORDS", "MAX_TOKENS",
                                           "REQUEST_TIMEOUT", "WARMUP_REQUESTS", "POLL_SECONDS", "COOLDOWN_SECONDS")},
        "steps": [],
    }
    print(f"모델 {meta['model']}, max_num_seqs={meta['max_num_seqs']}, KV 캐시 {meta['kv_cache_tokens']} 토큰")
    if meta["missing_metrics"]:
        print(f"경고: 찾지 못한 지표 {meta['missing_metrics']}. metrics_start.txt를 확인한다.", file=sys.stderr)

    for i in range(WARMUP_REQUESTS):
        print("워밍업", i + 1, one_request(meta["model"], 0, f"{RUN}-warmup-{i}")["status"])

    poller, rows, summary = Poller(), [], []
    poller.start()
    for conc in CONCURRENCY:
        wait_idle()
        step_rows, warm, done = [], threading.Event(), threading.Event()
        threads = run_step(meta["model"], conc, step_rows, warm, done)
        # 측정 구간은 처음 N건이 끝난 뒤부터다. 처리량·서버 측 평균·자원 지표 모두 이 구간으로 잡는다.
        warm.wait()
        start = time.time()
        before, r0 = vllm_metrics(), restarts(start)
        poller.step = conc
        done.wait()
        # 버리는 요청이 끝나기 전에 단계 종료 지표를 찍는다. 그래야 서버 측 평균에도 섞이지 않는다.
        end = time.time()
        poller.step = ""
        after, r1 = vllm_metrics(), restarts(end)
        for t in threads:
            t.join()
        rows += step_rows
        polls = [p for p in poller.rows if p["concurrency"] == conc]
        summary.append(summarize(conc, step_rows, polls, before, after, start, end, resources(start, end), r0, r1))
        meta["steps"].append({"concurrency": conc, "start": start, "end": end})
        print(json.dumps(summary[-1], ensure_ascii=False))
        # 중간에 끊겨도 끝난 단계는 남도록 단계마다 다시 쓴다.
        with open(os.path.join(OUT, "meta.json"), "w") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        write_csv("requests.csv", rows)
        write_csv("vllm_metrics.csv", poller.rows)
        write_csv("summary.csv", summary)
    poller.stop.set()
    print("결과:", OUT)


def selftest():
    text = ('# HELP x\nvllm:num_requests_running{model_name="a"} 1\nvllm:num_requests_running{model_name="b"} 2\n'
            'vllm:cache_config_info{block_size="16",num_gpu_blocks="100"} 1.0\nvllm:kv_cache_usage_perc 0.5\n')
    m = parse_metrics(text)
    assert m["vllm:num_requests_running"] == 3
    assert pick(m, GAUGES["kv_usage"]) == 0.5
    assert metric_labels(text, "vllm:cache_config_info") == {"block_size": "16", "num_gpu_blocks": "100"}
    assert pct(list(range(1, 41)), 95) == 38 and pct([7], 95) == 7 and pct([], 95) == ""
    print("selftest ok")


if __name__ == "__main__":
    selftest() if sys.argv[1:] == ["selftest"] else main()
