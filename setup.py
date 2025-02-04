from setuptools import setup, find_packages

packages = find_packages(where="src")
print("Discovered packages:", packages)

setup(
    name="flow",
    version="0.1.0",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    include_package_data=True,
    package_data={
        "flow.managers": ["*.yaml"],
    },
    install_requires=[
        # TODO(jaredquincy): list dependencies.
    ],
    entry_points={
        "console_scripts": [
            "flow=flow.main:main",
        ],
    },
)
