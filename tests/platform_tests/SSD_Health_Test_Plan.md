# SSD Health Test Plan

## Rev 0.1

- [Revision](#revision)
- [Overview](#overview)
  - [Scope](#scope)
  - [Testbed](#testbed)
  - [Related issue](#related-issue)
- [Setup configuration](#setup-configuration)
- [Test Cases](#test-cases)
  - [test_ssd_smart_health_baseline](#test-case-test_ssd_smart_health_baseline)
  - [test_ssd_write_stress_endurance](#test-case-test_ssd_write_stress_endurance)
  - [test_ssd_mixed_io_stress](#test-case-test_ssd_mixed_io_stress)

## Revision


| Rev | Date       | Author          | Change Description |
| --- | ---------- | --------------- | ------------------ |
| 0.1 | 2026-09-23 | Anton Hryshchuk | Initial version    |




## Overview

This follows the request in
[sonic-net/sonic-mgmt#23149](https://github.com/sonic-net/sonic-mgmt/issues/23149): sonic-mgmt
needs tests for SSD health, for all Spectrum platforms.

Right now sonic-mgmt has no test that:

- Reads and checks SSD/NVMe SMART health data (media errors, reallocated sectors, wear
level, temperature, power-on hours).
- Puts write/read load on the disk and checks the SMART counters and kernel logs still look
healthy afterwards.
- Checks I/O stats stay in a reasonable range under load.
- Tracks the SSD's endurance signal (wear level / media errors trend) across runs.

This test plan adds that as new test cases under `tests/platform_tests/`.

## Scope

These tests check SSD/NVMe health and behavior under I/O load on a physical SONiC DUT, and are
meant to run as part of normal regression together with the rest of the platform tests. The load
is put on a large file on the DUT's existing filesystem (e.g. under `/host` or another writable
mount).

What each test covers from the original gap description:

1. Disk health getting worse after back-to-back huge writes → `test_ssd_write_stress_endurance`.
2. Read/write stats look ok vs. limits → `test_ssd_mixed_io_stress`.
3. Extra I/O pressure to check disk health → `test_ssd_write_stress_endurance` /
  `test_ssd_mixed_io_stress`.
4. SSD endurance signal (wear level / media errors trend) → `test_ssd_smart_health_baseline`
  (single snapshot) plus `test_ssd_write_stress_endurance` / `test_ssd_mixed_io_stress`
   (baseline/delta comparison around the I/O load).



## Testbed

Any topology (`topology('any')`), physical DUT (`device_type('physical')`) — SMART/NVMe data
needs a real disk, so it doesn't make sense on a virtual switch (`vsonic`).

## Related issue

[SSD health check · Issue #23149 · sonic-net/sonic-mgmt](https://github.com/sonic-net/sonic-mgmt/issues/23149)

## Setup configuration

**Disk-detection fixture**
A session/module-scoped fixture finds the DUT's disk device(s) (`lsblk`, `nvme list`) and
checks if it's SSD/NVMe (`nvme smart-log`) or SATA/SCSI (`smartctl -a`).

**Utility-install fixture**
A fixture installs the utilities needed on the DUT if they're missing (`smartctl` from
`smartmontools`, `nvme-cli`, `fio`), and removes them again in teardown (`finally`/fixture
finalizer), no matter how the test ends, so the DUT is left as it was found.

**Skip instead of fail**
Tests skip (not fail) on platforms/DUTs where SMART/NVMe tools can't be installed or the disk
doesn't expose the expected health data.

**Shared parsing helper**
A shared helper module (e.g. `tests/platform_tests/ssd_health_utils.py`) parses
`smartctl -a`/`nvme smart-log` output into a small health-data dict, so all test cases reuse
the same parsing and threshold checks.

## Test Cases



### Test case: test_ssd_smart_health_baseline

Covers gap item 4 (endurance signal), as a single health snapshot with no load involved. This
test is fast (no I/O load) and is the one expected to run in every regular regression pass.

#### Test steps

1. Find the DUT's disk device and make sure SMART/NVMe tools are available (install if
  missing).
2. Read current health data via `smartctl -a` / `nvme smart-log`.
3. Parse: media/error count, reallocated sectors (SATA) or media errors (NVMe), percentage
  used / wear level count, temperature, power-on hours, critical warning flags.

#### Verify

- No critical warning flags are set.
- Media/reallocated errors are `0` (or below a documented acceptable threshold for the platform).
- Temperature is within the platform's safe range.
- Percentage used / wear level is below a configurable failure threshold (default e.g. 90%).



### Test case: test_ssd_write_stress_endurance

Covers gap item 1 (deterioration on back-to-back huge writes), item 3 (extra I/O pressure), and
item 4 (endurance signal).

#### Test steps

1. Capture baseline SMART/NVMe health data (reuses baseline logic from
  `test_ssd_smart_health_baseline`).
2. Run a bounded sequential-write `fio` job against a large file on an existing, already
  mounted filesystem (not a raw device), with a CI-friendly runtime (parametrized, default in  the order of minutes rather than the 1 hour).
3. Capture kernel log markers before/after via `dmesg` (or loganalyzer) to catch any I/O errors
  during the run.
4. Capture SMART/NVMe health data again after the run.
5. Clean up the test file.

#### Verify

- fio job finishes with no I/O errors and reports write bandwidth/IOPS greater than zero
(this is a sanity check, not a performance target).
- No new media errors / reallocated sectors compared to baseline.
- No NVMe/disk-related errors in `dmesg` during the run.
- Temperature stays within the safe range during/after the run.



### Test case: test_ssd_mixed_io_stress

Covers gap item 2 (read/write stats sanity), item 3 (extra I/O pressure), and item 4
(endurance signal).

#### Test steps

1. Capture baseline SMART/NVMe health data.
2. Run a bounded mixed random read/write `fio` job (parametrized read/write mix, block size,
  iodepth, runtime) against the same test file used in `test_ssd_write_stress_endurance`.
3. Sample `iostat -xm` during the run (or a fixed number of samples) to record utilization,
  average queue size and latency.
4. Capture SMART/NVMe health data again after the run.

#### Verify

- Read/write IOPS and latency from `fio`/`iostat` are within a reasonable range for the
platform: non-zero throughput, no fio error/verify failures.
- No new media errors / reallocated sectors compared to baseline.
- No NVMe/disk-related errors in `dmesg` during the run.
