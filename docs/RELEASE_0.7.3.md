# Release 0.7.3

## What changed

- promoted shadow mode to a first-class CLI workflow via `semzero shadow`
- added `--shadow-mode` as a compatibility alias for CI and scripted rollouts
- fixed the quickstart GitHub Action scaffold to use the working shadow workflow
- added `python -m semzero` entry support for simpler cross-device usage
- improved packaging/install docs for virtualenv, pipx, wheel installs, and Databricks extra
- added install helper scripts for Unix and PowerShell

## Why it matters

SemZero now has a cleaner default deployment posture: shadow mode first, then calibrated enforcement. The repository is also easier to install and use consistently across devices without relying on a shell entrypoint being present already.
