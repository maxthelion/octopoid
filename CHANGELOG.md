# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Agents now populate the `execution_notes` field when submitting tasks for completion. This field stores a concise summary of what the agent accomplished (commits made, turns used, PR status, and key actions) to help operators and dashboards understand task outcomes at a glance without reading full logs. [GH-13]
