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
    ],
    entry_points={
        "console_scripts": [
            "meister=meister.cli:main",
        ],
    },
    python_requires=">=3.9",
)
