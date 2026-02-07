"""Backward compatibility: validator role renamed to pre_check.

Import PreCheckRole from the new location. This file exists so that
any existing agents.yaml configs with role=validator continue to work.
"""

from .pre_check import PreCheckRole as ValidatorRole  # noqa: F401
from .pre_check import main  # noqa: F401

if __name__ == "__main__":
    main()
