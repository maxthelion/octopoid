"""Setup script for orchestrator package."""

from setuptools import find_packages, setup

setup(
    name="orchestrator",
    version="0.1.0",
    description="Local multi-agent scheduler for Claude Code",
    author="Your Name",
    author_email="your.email@example.com",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "pyyaml>=6.0",
    ],
    entry_points={
        "console_scripts": [
            "orchestrator-scheduler=orchestrator.scheduler:main",
            "orchestrator-init=orchestrator.init:init_orchestrator",
            "orchestrator-migrate=orchestrator.migrate:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
