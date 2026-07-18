import time
from .agent import ZDXNodeAgent


def main():
    node = ZDXNodeAgent()
    print(node.register())

    while True:
        time.sleep(30)


if __name__ == "__main__":
    main()
