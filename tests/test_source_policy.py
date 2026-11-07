import pytest

from project_evidence.source_policy import is_allowed_path, normalize_github_repository, SourceError


@pytest.mark.parametrize("path", ["LICENSE", "LICENCE.txt", "COPYING.md", "NOTICE", "pom.xml", "src/main/java/Example.java"])
def test_source_and_root_license_files_are_allowed(path: str) -> None:
    assert is_allowed_path(path)


@pytest.mark.parametrize("path", ["credentials.json", "config/application-private.yaml", "data/public.json", "node_modules/example/index.js", "unknown_extensionless", "nested/LICENSE"])
def test_private_generated_and_unknown_paths_are_excluded(path: str) -> None:
    assert not is_allowed_path(path)


@pytest.mark.parametrize("url", ["https://github.com/Example/Public-Java.git", "git@github.com:Example/Public-Java.git", "ssh://git@github.com/Example/Public-Java.git"])
def test_standard_origin_forms_identify_one_repository(url: str) -> None:
    assert normalize_github_repository(url) == "https://github.com/example/public-java"


@pytest.mark.parametrize("url", ["https://user:synthetic@github.com/example/repo", "https://github.com/example/repo?token=synthetic", "https://other.example/example/repo", "file:///private/repo", "ssh://git@github.com:2222/example/repo"])
def test_unsupported_origins_are_rejected(url: str) -> None:
    with pytest.raises(SourceError) as captured:
        normalize_github_repository(url)
    assert captured.value.code == "invalid_source"
