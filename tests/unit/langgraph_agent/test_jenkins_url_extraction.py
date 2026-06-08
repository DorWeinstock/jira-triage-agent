"""Tests for Jenkins URL extraction from ticket text."""

import pytest

from src.utils.jenkins_url_extractor import extract_jenkins_urls


class TestExtractJenkinsURLs:
    def test_extract_plain_url(self):
        text = "Build failed: https://jenkins.example.com/job/my-job/123/"
        result = extract_jenkins_urls(text)
        assert len(result) == 1
        assert "jenkins.example.com" in result[0]
        assert "my-job" in result[0]
        assert "123" in result[0]

    def test_extract_nested_folder_url(self):
        text = "See https://sw-jenkins.com/job/folder/job/sub/job/name/456/"
        result = extract_jenkins_urls(text)
        assert len(result) == 1
        assert "sw-jenkins.com" in result[0]
        assert "456" in result[0]

    def test_extract_jira_wiki_markup(self):
        text = "[Build #123|https://jenkins.example.com/job/my-job/123/]"
        result = extract_jenkins_urls(text)
        assert len(result) == 1
        assert "jenkins.example.com" in result[0]

    def test_extract_markdown_link(self):
        text = "[Build #123](https://jenkins.example.com/job/my-job/123/)"
        result = extract_jenkins_urls(text)
        assert len(result) == 1
        assert "jenkins.example.com" in result[0]

    def test_extract_multiple_urls(self):
        text = """Build 1: https://jenkins.example.com/job/job-a/1/
        Build 2: https://jenkins.example.com/job/job-b/2/
        Build 3: https://jenkins.example.com/job/job-c/3/"""
        result = extract_jenkins_urls(text)
        assert len(result) == 3

    def test_extract_no_jenkins_url(self):
        text = "No build link here, just some text."
        result = extract_jenkins_urls(text)
        assert result == []

    def test_extract_non_jenkins_url_ignored(self):
        text = "See https://github.com/repo/issues/1 for details"
        result = extract_jenkins_urls(text)
        assert result == []

    def test_extract_url_without_trailing_slash(self):
        text = "https://jenkins.example.com/job/my-job/123"
        result = extract_jenkins_urls(text)
        assert len(result) == 1

    def test_extract_url_with_http(self):
        text = "http://ci.internal.com/job/build-pipeline/42/"
        result = extract_jenkins_urls(text)
        assert len(result) == 1
        assert "ci.internal.com" in result[0]

    def test_extract_deduplicates(self):
        text = """https://jenkins.example.com/job/my-job/123/
        and again https://jenkins.example.com/job/my-job/123/"""
        result = extract_jenkins_urls(text)
        assert len(result) == 1

    def test_extract_url_with_query_params(self):
        text = "https://jenkins.example.com/job/my-job/123/?retry=1"
        result = extract_jenkins_urls(text)
        assert len(result) == 1
        # URL should be the build URL, not include query params
        assert "?" not in result[0]
