from bumpscope import project


def test_python_files_skips_environments_and_build_output(make_project):
    root = make_project(
        {
            "app/main.py": "",
            "app/data.txt": "",
            "tests/test_main.py": "",
            "setup.py": "",
            ".git/hooks/x.py": "",
            ".tox/x.py": "",
            "build/lib/x.py": "",
            "dist/x.py": "",
            "app/__pycache__/x.py": "",
            "env/pyvenv.cfg": "",
            "env/lib/x.py": "",
        },
        installed={"demo": "1.0"},
    )

    files = [p.relative_to(root).as_posix() for p in project.python_files(root)]

    assert files == ["setup.py", "app/main.py", "tests/test_main.py"]
