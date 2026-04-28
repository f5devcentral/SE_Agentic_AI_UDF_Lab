# Phase 1: Security Guide

AI components are not integrated during Phase 1. Standard application security protocols are applied to protect the baseline 3-tier structure via F5 BIG-IP WAF (AWAF).

## Attack Surface

The initial layout exposes common web vulnerabilities delineated by the OWASP Top 10 framework:
- **SQL Injection**: Input vectors targeting the backend PostgreSQL database.
- **Cross-Site Scripting (XSS)**: Payload injection points on the frontend endpoints.
- **API Abuse**: Volumetric or brute force attacks against non-rate-limited backend endpoints.

### BIG-IP AWAF Mitigations

A baseline protective policy on the BIG-IP enforces the following mitigations:
- Parameter enforcement for flight and hotel query URLs.
- SQL injection execution blocking on database requests.
- Traffic rate limiting configurations assigned directly to the Virtual Server.

### Preparatory Security Posture

Subsequent integration of the Model Context Protocol (MCP) in Phase 2 introduces vulnerabilities regarding unauthorized remote command executions. Ensure K3s networks and Virtual Servers adhere to strict routing policies prior to executing deployment phases that map external tooling.
