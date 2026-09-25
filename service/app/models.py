def number(value):
    try:
        return float(str(value).replace("%", ""))
    except (ValueError, TypeError):
        return None


def pick(item, *keys):
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return None


def process_kind(command):
    cmd = str(command).strip().lower()
    words = cmd.split()
    head = words[0].rsplit('/', 1)[-1] if words else ''
    if head == 'env' and len(words) > 1:
        head = words[1].rsplit('/', 1)[-1]
    if head.startswith('python'):
        return 'Python'
    if head == 'julia':
        return 'Julia'
    if head == 'rscript':
        return 'Rscript'
    if head == 'java':
        return 'Java'
    if head in ('mpirun', 'mpiexec'):
        return 'MPI'
    if head in ('bash', 'sh') and '.sh' in cmd:
        return 'shell script'
    return 'other'


def parse_top(data):
    titles = data.get("Titles") or data.get("titles") or []
    rows = data.get("Processes") or data.get("processes") or []
    if not isinstance(titles, list) or not isinstance(rows, list):
        return []
    cols = [str(x).lower().replace("%", "").replace(" ", "") for x in titles]
    result = []
    for row in rows:
        if not isinstance(row, list):
            continue
        item = dict(zip(cols, row))
        command = str(item.get("args") or item.get("command") or item.get("cmd") or "")
        result.append({"pid": item.get("pid"), "cpu_percent": number(pick(item, "cpu", "pcpu")), "memory_percent": number(pick(item, "mem", "pmem")), "elapsed": item.get("etime") or item.get("elapsed"), "command": command, "kind": process_kind(command)})
    return sorted(result, key=lambda p: p["cpu_percent"] or 0, reverse=True)


def parse_stats(data, container_id):
    if isinstance(data, dict):
        rows = data.get("stats") or data.get("Stats") or [data]
    else:
        rows = data if isinstance(data, list) else []
    if not isinstance(rows, list):
        rows = [rows]
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = pick(row, "ContainerID", "container_id", "id")
        if rid is None and len(rows) != 1:
            continue
        if rid and not (str(container_id).startswith(str(rid)) or str(rid).startswith(str(container_id))):
            continue
        cpu = number(pick(row, "CPU", "cpu", "CPUPercent"))
        used = number(pick(row, "MemUsage", "mem_usage", "MemUsageBytes"))
        limit = number(pick(row, "MemLimit", "mem_limit"))
        # Podman can report host RAM for an unconstrained container; caller replaces it with inspect HostConfig.Memory.
        return {"cpu_percent": cpu, "equivalent_cpus": round(cpu / 100, 2) if cpu is not None else None, "memory_used": used, "memory_limit_reported": limit, "pids": pick(row, "PIDs", "pids")}
    return {"cpu_percent": None, "equivalent_cpus": None, "memory_used": None, "memory_limit_reported": None, "pids": None}
