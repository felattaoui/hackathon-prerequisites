# Lab region decision

Decision date: 2026-10-01. Decision: **West Europe (`westeurope`)** for the lab.
North Europe is qualified separately and is **not** validated by any lab result in West Europe.

Re-run the evidence at any time: `python -m hapq preflight`, `python -m hapq docs --criterion C05.a --criterion C05.b`,
or notebook `notebooks/00_preflight_and_region.ipynb`.

## Candidates examined

The brief asked for West Europe to be examined first, without assuming it fits.

| Requirement | West Europe | Evidence type | Source |
|---|---|---|---|
| Listed for hosted agents | Yes | documented | [hosted agents — region availability](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents#region-availability) (`DOC-C05-WESTEUROPE`) |
| Agent Service + private VNet + private class A | Yes | documented | [limits, quotas and regions](https://learn.microsoft.com/azure/foundry/agents/concepts/limits-quotas-regions#supported-regions), [virtual networks — limitations](https://learn.microsoft.com/azure/foundry/agents/how-to/virtual-networks#limitations) |
| ARM offers Foundry projects | Yes | observed | `PF-ARM-LOCATIONS` (provider metadata) |
| Model offered | `gpt-5-mini` 2025-08-07, GA, `DataZoneStandard` and `GlobalStandard` | observed | `PF-MODEL-AVAILABLE` |
| Quota for the lab deployment (10K TPM DataZoneStandard) | Available (limit 300K TPM, unused) | observed | `PF-MODEL-QUOTA` |
| Subscription policies | No location restriction applies | observed | `PF-POLICIES` |
| Resource providers | All required ones registered | observed | `PF-PROVIDERS` |
| Hosted agent actually created and active | **Not yet demonstrated** | — | `S01-GA-CREATE` (Phase A, pending approval) |

`DataZoneStandard` (EU data zone) is preferred over `GlobalStandard` to keep inference processing within the EU data zone.
Older models (`gpt-4o-mini`, `gpt-4.1-mini`) show lifecycle *Deprecating* / *Legacy* in this region and were not chosen.

Because West Europe satisfies every documented and subscription-level requirement, no non-European alternative
was needed.

## Documented availability vs demonstrated capacity

- *Documented* means Microsoft Learn lists the region on the fetch date.
- *Observed* preflight results show the subscription allows the deployment (model offered, quota, policies).
- Neither proves capacity. West Europe becomes *demonstrated* only when a hosted-agent version reaches `active`
  there (`S01-GA-CREATE`). Quota is not a capacity guarantee.

## North Europe (criterion C05.b), tracked independently

| Check | Result on 2026-10-01 | Evidence |
|---|---|---|
| Listed in hosted-agent regions | **Not listed** (FAIL) | documented (`DOC-C05-NORTHEUROPE`) |
| Listed in Agent Service regional table | **Not listed** (FAIL) | documented (`DOC-C05-NORTHEUROPE-PRIVATE-VNET`) |
| ARM offers Foundry projects | Offered, but proves nothing about hosted agents | observed (`PF-ARM-LOCATIONS-REQUIRED`, NOT_TESTED) |
| Hosted agent deployed | Not executed by design (no deployment only to probe availability) | `NE-NOT-DEPLOYED` |

The documentary checks are re-executable: if Microsoft adds North Europe to the list, the next run turns them PASS.
A PASS in West Europe never changes the North Europe status.

## Region of each resource

The resource group `rg-hackathon-prerequisites-eaf` was created in `westeurope` (metadata only). Every Bicep
module takes `location` as a required parameter without default, and `PF-RG` lists the location of each resource
inside the group. Global resources (private DNS zones, the Azure Monitor private link scope) are `global` by design.
