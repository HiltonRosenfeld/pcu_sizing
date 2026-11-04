# Wiki Index

This wiki captures project context and working preferences for the PCU Sizing tool.

## Files

| File | Description |
|------|-------------|
| [project.md](project.md) | Project overview, goals, stakeholders, and key modules |
| [preferences.md](preferences.md) | Working standards, coding style, and AI collaboration preferences |

## Quick Summary

An internal IBM web app for capacity planning on **Astra DB Serverless**. Given a set of databases with known on-demand metrics, it **maps the databases onto PCU groups** (PCU type, count, and which databases share each group) at the lowest reserved (RCU) cost, within each PCU type's limits (80% utilisation ceiling, ≤5 PCUs per group target, 10 hard cap). Currently in early stages — the mapping model is defined; the tool is being built out.
