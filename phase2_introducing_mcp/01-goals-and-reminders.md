# Phase 2: Goals and Reminders

## Objective
Introduce the Model Context Protocol (MCP) to the existing 3-tier travel application. This phase configures specific MCP servers (Travel and Weather) to standardize external tool execution for downstream AI components.

## Prerequisites
- Operational K3s cluster configured with the MCP Context.
- Local registry deployed at NodePort 30500.
- `kubectl` configured with cluster access.
- Docker installed on the build machine.

## Kubernetes Context
Set the appropriate namespace and context for Phase 2:
```bash
kubectl config get-contexts
kubectl config use-context MCP
```
