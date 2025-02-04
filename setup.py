from setuptools import setup, find_packages

packages = find_packages(where="src")
print("Discovered packages:", packages)

setup(
    name="flow",
    version="0.1.2",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    include_package_data=True,
    package_data={
        "flow.managers": ["*.yaml"],
    },
    install_requires=[
        "parameterized==0.9.0",
        "pydantic==2.10.4",
        "pydantic-settings>=2.0,<3.0",
        "pytest==8.3.3",
        "python-json-logger==2.0.7",
        "PyYAML==6.0.2",
        "requests==2.32.3",
        "responses==0.25.3",
        "rich==13.9.4",
        "setuptools==75.2.0",
        "jupyter==1.0.0",
        "tabulate==0.9.0",
    ],
    entry_points={
        "console_scripts": [
            "flow=flow.main:main",
        ],
    },
)
