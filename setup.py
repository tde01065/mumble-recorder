"""Setup configuration for mumble-recorder."""
from setuptools import setup, find_packages

setup(
    name="mumble-recorder",
    version="0.1.0",
    description="Headless Mumble channel recorder",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "pymumble>=1.6.1",
    ],
    python_requires=">=3.11",
)
