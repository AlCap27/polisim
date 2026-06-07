# PoliSim — Open Civic Intelligence for Democratic Communication

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Civic Tech Guide](https://img.shields.io/badge/Civic%20Tech-Guide-green.svg)](https://civictech.guide)

Production-grade civic infrastructure for values-based communication on open public data.

## What is PoliSim?

PoliSim connects:

- **Open census data** (ISTAT 2021, 71M observations across 139 constituencies)
- **Electoral records** (Italian Ministry of Interior Eligendo OpenData)
- **Psychographic segmentation** (ITANES, ESS, Tripol)
- **AI with declared limitations** (substitutable by design)

To help civic organisations (NGOs, trade unions, municipalities) test whether their messages are coherent with their stated values and appropriate for the demographic reality of their target territory.

**Live production system:** [polisim.dev](https://polisim.dev)  
**Documentation:** [polisim.dev/metodologia.html](https://polisim.dev/metodologia.html)  
**Civic Tech Directory:** [Civic Tech Field Guide](https://civictech.guide)

---

## Key Features

### 🎯 Bayesian MRP Electoral Model
- 220 constituencies (Camera + Senato)
- RMSE 4.3pp out-of-sample (Lombardia 2023 blind test)
- Full posterior distributions with credibility intervals

### 🔍 Values-Coherence Evaluation
Tests whether a message is coherent with an organisation's stated principles and appropriate for target territory demographics.

### 🔄 AI Provider Substitutability
Claude / Mistral / LLaMA / Phi / Ollama — swap in minutes, zero infrastructure changes.

### 📊 Open Public Data Sources

**Italian Electoral & Census Data:**
- ISTAT Permanent Census 2021 (71M observations, 139 constituencies)
- Ministry of Interior Eligendo OpenData (electoral results 2018-2025)
- ITANES 2022 empirical voter segmentation (DOI: 10.13130/RD_UNIMI/JV77WR)

**European Psychographic Data:**
- European Social Survey (ESS) — Cross-national attitudinal data
- Tripol dataset — Political psychology and value orientations

**Scalability:**
- Architecture designed for Eurostat integration (France, Germany, Spain)
- No proprietary data sources
- GDPR-compliant by design

### 🔬 Declared Limitations

Four methodological caveats published openly:

1. Partially expert-calibrated demographic weights (±0.3-0.5pp impact)
2. Training set imbalanced toward Northern Italian regions
3. Coherence scores not validated on real campaign outcomes
4. **Self-evaluation framing bias:** the same LLM that generates message variants also scores them on Entman's four framing dimensions — a known confirmation bias in LLM self-annotation (Ziems et al., 2023, *Computational Linguistics*, doi:[10.1162/coli_a_00502](https://doi.org/10.1162/coli_a_00502))

**Methodological foundations:**
- Framing model: Entman (1993), *Journal of Communication*, doi:[10.1111/j.1460-2466.1993.tb01304.x](https://doi.org/10.1111/j.1460-2466.1993.tb01304.x) — 15,373 citations
- Psychographic simulation: Argyle et al. (2023), *Political Analysis*, doi:[10.1017/pan.2023.2](https://doi.org/10.1017/pan.2023.2) — mean reversion limitation declared

---

## Production Status

- **Deployment:** [polisim.dev](https://polisim.dev) (live since April 2026)
- **User Verticals:** 3 active (political movement, trade union, international NGO)
- **Validation:** Field testing in progress (May 2026)
- **Funding:** Applicant to NGI Zero Commons Fund (code 2026-06-238)

**Proof of Concept Evolution:**

| Version | Date | Method | Scope | RMSE |
|---|---|---|---|---|
| PoC 1 | March 2026 | OLS regression | 11 Lazio constituencies | 3.9pp |
| PoC 2 | April 2026 | Ridge regression | 142 national constituencies | 5.2pp |
| PoC 3 | May 2026 | Bayesian MRP | 220 constituencies | 4.3pp |

---

## Recognition & Validation

**Civic Tech Directory:**  
Listed in [Civic Tech Guide](https://civictech.guide) — Curated directory of recognized civic technology projects

**Research Network:**  
Member of [Anthropic Claude Partner Network](https://anthropic.com/partners) (June 2026)

**AI Partnership:**  
Member of [Anthropic Claude Partner Network](https://anthropic.com/partners) (June 2026)

**Data Access:**  
Meta Content Library — Approved researcher access via CASD/IDAN (May 2026)

**Institutional Validation:**  
Field testing partnerships: Trade union confederation + International NGO (May 2026)  
Beta deployment in three user verticals (political movement, labor organization, nonprofit)

**Funding Pipeline:**  
NGI Zero Commons Fund applicant (code 2026-06-238)

---

## Architecture

```
┌──────────────────┐
│   Census Data    │  ISTAT 2021 (71M observations)
│    (ISTAT)       │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐    ┌─────────────────────┐
│   MRP Model      │◄───│  Electoral Records  │  Eligendo OpenData
│    (PyMC)        │    │    (Eligendo)        │
└────────┬─────────┘    └─────────────────────┘
         │
         │              ┌─────────────────────┐
         ├──────────────│  Psychographic      │  ESS, Tripol, ITANES
         │              │  Segmentation       │
         │              └─────────────────────┘
         ▼
┌──────────────────┐    ┌─────────────────────┐
│   API Layer      │◄───│   AI Provider       │  Claude/Mistral/LLaMA
│   (FastAPI)      │    │  (Substitutable)    │
└────────┬─────────┘    └─────────────────────┘
         │
         ▼
┌──────────────────┐
│  Web Interface   │  polisim.dev
└──────────────────┘
```

**Tech Stack:**
- Python 3.11+
- PyMC (Bayesian inference)
- FastAPI (REST API)
- ISTAT OpenData APIs
- ESS + Tripol datasets
- Anthropic Claude API (substitutable)

---

## Quick Start

> **Note:** Full open-source release scheduled Q3 2026. Current repository structure is being prepared for public release. Installation guide will be available with the release.

Preview production system:
- Web interface: [polisim.dev](https://polisim.dev)
- Methodology: [polisim.dev/metodologia.html](https://polisim.dev/metodologia.html)

---

## AI Provider Substitutability

PoliSim is designed for AI provider independence. The system uses a provider-agnostic interface that allows swapping between commercial APIs and local models.

**Supported Providers:**
- Anthropic Claude (current production)
- Mistral AI
- Meta LLaMA (via Ollama)
- Microsoft Phi
- Any locally-deployed instruction-following LLM

Swap procedure (conceptual, full implementation in Milestone 1):

```python
# Current: Claude Sonnet
from polisim.ai import ClaudeProvider
ai = ClaudeProvider(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Swap to Mistral
from polisim.ai import MistralProvider
ai = MistralProvider(api_key=os.getenv("MISTRAL_API_KEY"))

# Swap to local LLaMA via Ollama
from polisim.ai import OllamaProvider
ai = OllamaProvider(model="llama3.1")
```

This architecture ensures no vendor lock-in and supports future EU AI Act compliance with European foundation models.

---

## Retraining on Your National Data

PoliSim's architecture generalizes to any EU democracy with comparable open data infrastructure.

**Supported (or planned) electoral systems:**

| Country | Status | Constituencies | Data Sources |
|---|---|---|---|
| 🇮🇹 Italy | Production | 220 | ISTAT + Eligendo + ITANES |
| 🇫🇷 France | Planned | circonscriptions législatives | INSEE + ESS |
| 🇩🇪 Germany | Planned | Wahlkreise | Destatis + ESS |
| 🇪🇸 Spain | Planned | circunscripciones | INE + ESS |
| 🇬🇧 UK | Feasible | FPTP constituencies | ONS + Electoral Commission + ESS |

Conceptual retraining workflow:

```bash
# 1. Prepare census data
python scripts/prepare_census.py --input census_FR.csv --country FR

# 2. Prepare electoral results
python scripts/prepare_elections.py --input elections_FR.csv --country FR

# 3. Integrate ESS psychographic data
python scripts/prepare_ess.py --input ess_FR.csv --country FR

# 4. Train MRP model
python scripts/train_mrp.py \
  --census processed/census_FR.csv \
  --elections processed/elections_FR.csv \
  --psychographic processed/ess_FR.csv \
  --output models/model_FR.pkl

# 5. Deploy with national model
python -m polisim.api --model models/model_FR.pkl --country FR
```

Full retraining documentation will be included in the Q3 2026 release.

---

## Documentation

**Live System:**
- [polisim.dev](https://polisim.dev) — Public web interface
- [polisim.dev/metodologia.html](https://polisim.dev/metodologia.html) — Methodology (EN/IT)

**Research:**

---

## Use Cases

### Trade Unions
Test whether a message on minimum wage or workers' rights is coherent with the union's statute and appropriate for the demographic reality of target regions.

### NGOs
Verify message-mission alignment before launching donor campaigns. Avoid strategic mistakes where message contradicts stated organizational values.

### Municipal Administrations
Evaluate citizen engagement strategies on census data rather than opaque commercial targeting tools.

### Political Movements
[Q-Italia](https://qitalia.org) uses PoliSim as its live proof-of-concept: all content is generated with AI assistance, tested for coherence with 14 constitutional principles, and published only after mandatory human approval.

---

## Project Roadmap

### ✅ Completed (Q1–Q2 2026)
- Bayesian MRP production deployment
- Multi-step coherence evaluation pipeline
- ESS/Tripol psychographic integration
- Field validation partnerships (trade union + NGO)
- GDPR-compliant data anonymization tool (Data Shield)
- Public methodology documentation with declared limitations
- Listed in Civic Tech Field Guide directory

### 🚧 In Progress (Q2 2026)
- Open-source repository preparation
- AI provider abstraction layer (Mistral/LLaMA/Ollama support)
- Academic peer review coordination
- Field validation case study publication

### 📅 Planned (Q3–Q4 2026, subject to NGI funding)
- Full open-source release
- Shapefile-level MRP (spatial poststratification)
- Party-level model (individual parties vs coalition aggregates)
- EU electoral adapter (France + Germany proof-of-concept)
- Academic validation paper submission

---

## Funding & Sustainability

**Current Status:** Self-funded development (October 2025 – May 2026)

**Applied Funding:**  
NGI Zero Commons Fund — Application code 2026-06-238

**Sustainability Model:**  
PoliSim is released under AGPL v3. The core infrastructure — MRP model, API, documentation — will always be free for self-hosting. Optional professional support services may be offered for mission-critical institutional deployments (following the Red Hat model: free self-hosting, paid support for enterprise use cases).

---

## Contributing

Full contribution guidelines will be published with the Q3 2026 open-source release.

For now, if you are interested in:
- Institutional validation partnerships (NGOs, trade unions, municipalities)
- Academic peer review (statistics, political science, computational social science)
- EU electoral adapter development (France, Germany, Spain data expertise)

Please contact: info@polisim.dev

---

## License

Released under the **GNU Affero General Public License v3.0 (AGPL v3)**.

This means:
- ✅ Free to use, modify, and self-host
- ✅ Free for NGOs, civic organisations, research institutions
- ⚠️ Any modified version deployed as a network service must release source code under AGPL v3
- 📧 Commercial licensing available for closed deployments: info@polisim.dev

See [LICENSE](LICENSE) for full terms.

---

## Citation

```bibtex
@software{polisim2026,
  author = {Capetola, Alessandro},
  title = {PoliSim: Open Civic Intelligence for Democratic Communication},
  year = {2026},
  url = {https://polisim.dev},
  note = {Bayesian MRP electoral forecasting on open public data}
}
```

---

## Contact

- **Web:** [polisim.dev](https://polisim.dev) · [qitalia.org](https://qitalia.org)
- **Email:** info@polisim.dev
- **NGI Application:** 2026-06-238
- **Civic Tech Field Guide:** [Civic Tech Field Guide](https://civictech.guide)

**Live Proof-of-Concept:** [Q-Italia](https://qitalia.org) — Political movement with 14 constitutional principles using PoliSim infrastructure for values-anchored communication.

---

## Acknowledgments

**Data Sources:**
- [ISTAT](https://www.istat.it) — Permanent Census 2021
- [Ministry of Interior](https://elezionistorico.interno.gov.it) — Eligendo OpenData
- [ITANES 2022](https://doi.org/10.13130/RD_UNIMI/JV77WR) — Empirical voter segmentation
- [European Social Survey](https://www.europeansocialsurvey.org) — Cross-national attitudinal data
- [Tripol](https://tripol.eu) — Political psychology and value orientations

**Research Network:**
- [Civic Tech Field Guide](https://civictech.guide) — Recognized civic technology directory
- [NGI Zero Commons Fund](https://nlnet.nl/commonsfund/) — Applied funding

**AI Foundation:**
- [Anthropic Claude](https://anthropic.com) — Current production AI provider
- Architecture designed for multi-provider support (Mistral, LLaMA, Phi, Ollama)

---

*Built with: Python 3.11+ · PyMC · FastAPI · ISTAT OpenData · Eligendo OpenData · ESS · Tripol · Claude API*

*Last updated: June 2026 · Version: Pre-release (open-source Q3 2026)*
