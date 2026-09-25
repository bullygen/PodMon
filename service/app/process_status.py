"""Conservative, configuration-free process and activity hints."""
from datetime import datetime, timezone

IDLE_COMMANDS = {'sleep', 'pause', 'tini', 'dumb-init', 'tmux', 'sshd', 'bash', 'sh'}
COMPUTE_COMMANDS = {'python', 'python3', 'julia', 'rscript', 'java', 'mpirun', 'mpiexec', 'matlab', 'octave'}


def candidate(process):
    command = str(process.get('command') or '').strip()
    if not command:
        return False
    words = command.split()
    first = words[0].rsplit('/', 1)[-1].lower()
    if first == 'env' and len(words) > 1:
        first = words[1].rsplit('/', 1)[-1].lower()
    if first in IDLE_COMMANDS and not (first in {'bash', 'sh'} and '.sh' in command):
        return False
    return (first in COMPUTE_COMMANDS or first.startswith('python') or (process.get('cpu_percent') or 0) >= 5)


def assess(processes, container_status, last_log_at, top_available=True, stale_after_seconds=1800):
    result = {'status': 'unknown', 'activity': 'unknown', 'candidates': [], 'last_activity_seconds': None}
    if container_status in ('exited', 'dead'):
        result['status'] = 'stopped'
        return result
    if container_status != 'running' or not top_available:
        return result
    result['candidates'] = [p for p in processes if candidate(p)]
    result['status'] = 'candidate' if result['candidates'] else 'no_candidate'
    if last_log_at:
        try:
            result['last_activity_seconds'] = max(0, int(datetime.now(timezone.utc).timestamp() - datetime.fromisoformat(last_log_at.replace('Z', '+00:00')).timestamp()))
        except (TypeError, ValueError):
            pass
    if any((p.get('cpu_percent') or 0) >= 5 for p in result['candidates']):
        result['activity'] = 'active'
    elif result['status'] == 'candidate' and result['last_activity_seconds'] is not None:
        result['activity'] = 'active' if result['last_activity_seconds'] < stale_after_seconds else 'quiet'
    return result
