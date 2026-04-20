import subprocess

try:
    from bootstrap import repo_root
except ModuleNotFoundError:
    from utilities.bootstrap import repo_root


TARGET_BRANCH = "main"
GIT_TIMEOUT_SECONDS = 60


def _run(cmd):
    print(f"running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=repo_root(), text=True, timeout=GIT_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError(f"command failed with code {result.returncode}: {' '.join(cmd)}")
    return result


def _capture(cmd):
    result = subprocess.run(
        cmd,
        cwd=repo_root(),
        text=True,
        capture_output=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if stderr:
            print(stderr)
        raise RuntimeError(f"command failed with code {result.returncode}: {' '.join(cmd)}")
    return result.stdout.strip()


def _head_sha(ref):
    return _capture(["git", "rev-parse", ref])


def main():
    _run(["git", "fetch", "origin", TARGET_BRANCH])

    local_head = _head_sha("HEAD")
    remote_head = _head_sha(f"origin/{TARGET_BRANCH}")

    print(f"local HEAD: {local_head}")
    print(f"remote HEAD: {remote_head}")

    if local_head == remote_head:
        print("no updates found")
        return

    print("updates found, pulling latest code")
    _run(["git", "pull", "--ff-only", "origin", TARGET_BRANCH])
    print("update completed, uvicorn reload should apply code changes")


if __name__ == "__main__":
    main()