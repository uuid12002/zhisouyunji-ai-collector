import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent


def main():
    children = []
    try:
        try:
            with urlopen('http://127.0.0.1:8765/health', timeout=1):
                raise SystemExit('Port 8765 already in use. Stop the existing service first.')
        except OSError:
            pass
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', '8765'], cwd=ROOT)
        children.append(server)
        for _ in range(60):
            if server.poll() is not None:
                raise RuntimeError('API failed to start')
            try:
                with urlopen('http://127.0.0.1:8765/health', timeout=1):
                    break
            except OSError:
                time.sleep(.25)
        else:
            raise RuntimeError('API startup timed out')
        worker = subprocess.Popen([sys.executable, 'real_worker.py'], cwd=ROOT)
        children.append(worker)
        children.append(subprocess.Popen([sys.executable, 'worker.py'], cwd=ROOT))
        webbrowser.open('http://127.0.0.1:8765')
        print('Local collector: http://127.0.0.1:8765 | Ctrl+C to stop', flush=True)
        while all(p.poll() is None for p in children):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == '__main__':
    main()
