import pytest

from bumpscope import project


def test_installed_version(make_project):
    root = make_project({}, installed={"demo": "1.0", "other": "2.0"})

    assert project.installed_version(root, "demo") == "1.0"


def test_installed_version_normalizes_names(make_project):
    root = make_project({}, installed={"PyYAML": "6.0.2", "my_pkg": "0.3"})

    assert project.installed_version(root, "pyyaml") == "6.0.2"
    assert project.installed_version(root, "My-Pkg") == "0.3"


def test_installed_version_windows_layout(tmp_path):
    dist_info = tmp_path / ".venv" / "Lib" / "site-packages" / "demo-1.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text("Name: demo\nVersion: 1.0\n")

    assert project.installed_version(tmp_path, "demo") == "1.0"


def test_no_venv(make_project):
    root = make_project({}, installed=None)

    with pytest.raises(project.ProjectError, match="No .venv found.*--from"):
        project.installed_version(root, "demo")


def test_package_not_installed(make_project):
    root = make_project({}, installed={"other": "1.0"})

    with pytest.raises(project.ProjectError, match="demo is not installed.*--from"):
        project.installed_version(root, "demo")


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
