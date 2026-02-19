"""Entry point: python -m packages.dashboard"""

from .app import OctopoidDashboard


def main() -> None:
    app = OctopoidDashboard()
    app.run()


if __name__ == "__main__":
    main()
