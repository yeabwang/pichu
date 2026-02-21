# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.2] - 2026-02-21

### Added
- Added main CLI runtime functionality and session management with workspace trust and session resumption support.
- Added UI capabilities/contracts and event handling infrastructure for richer terminal interactions.
- Added one-line Windows installers and improved installation workflow support.
- Added workspace safety management.
- Added comprehensive tests for runtime task execution and UI capabilities.
- Added a TDD guide and transcript management for sub-agent runs.

### Changed
- Refactored TUI input handling and UI rendering for improved asynchronous interactions.
- Standardized command output handling across commands using `CommandDisplayPayload`.
- Enhanced the `uv` installation flow with package-manager-aware handling and improved resume guidance.
- Updated Python support and references to 3.11+ across project config, CI, and Docker.
- Refactored code structure and standardized capitalization of "pichu" in docs/code.
- Updated and reorganized README/configuration documentation.

### Fixed
- Fixed installed CLI entrypoint behavior.
- Improved project initialization and modular scaffolding command behavior.
- Fixed alias validation by updating the regex checks.

