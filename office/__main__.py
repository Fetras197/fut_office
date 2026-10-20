import sys

from . import cloud

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "cloud"
    sys.exit(cloud.main(cmd, sys.argv[2:]))
