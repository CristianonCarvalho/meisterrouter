from setuptools import setup, find_packages

setup(
    name="meisterrouter",
    version="1.0.0",
    description="Multi-Model Autonomous Orchestration Framework for Claude Code & OpenAI Codex",
    author="Cristiano Carvalho",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        "requests>=2.31.0",
        "flask>=3.0.0",
        "python-dotenv>=1.0.0",
        "click>=8.0.0",
        "PyYAML>=6.0",
        "pydantic>=2.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=8.0.0",
            "pytest-asyncio>=0.23.0",
            "ruff>=0.1.0",
            "mypy>=1.10.0",
            "types-PyYAML>=6.0.0",
            "types-requests>=2.31.0",
        ]
    },
    entry_points={
        "console_scripts": [
            "meister=meister.cli:main",
        ],
    },
    python_requires=">=3.9",
)
