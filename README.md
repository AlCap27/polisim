# PoliSim — Open Civic Intelligence for Democratic Communication

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Civic Tech Guide](https://img.shields.io/badge/Civic%20Tech-Guide-blue)](https://civictech.guide/)

Production-grade civic infrastructure for values-based communication on open public data.

## What is PoliSim?

PoliSim connects:
- **Open census data** (ISTAT 2021, 71M observations across 139 constituencies)
- **Electoral records** (Italian Ministry of Interior Eligendo OpenData)
- **Psychographic segmentation** (ITANES, ESS, Tripol)
- **AI with declared limitations** (substitutable by design)

To help civic organisations (NGOs, trade unions, municipalities) test whether their messages are coherent with their stated values and appropriate for the demographic reality of their target territory.

**Live production system:** [api2.polisim.dev](https://api2.polisim.dev)  
**Documentation:** [polisim.dev/metodologia.html](https://polisim.dev/metodologia.html)  
**Civic Tech Directory:** [civictech.guide](https://civictech.guide/)

---

## Key Features

### 🎯 Bayesian MRP Electoral Model
- 220 constituencies (Camera + Senato)
- RMSE 4.3pp out-of-sample (Lombardia 2023 blind test)
- Full posterior distributions with credibility intervals

### 🔍 Values-Coherence Evaluation
Tests whether a message is coherent with an organisation's stated principles and appropriate for target territory demographics.

### 🔄 AI Provider Substitutability
**Claude** / **Mistral** / **LLaMA** / **Phi** / **Ollama** — swap in minutes, zero infrastructure changes.

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
Three methodological caveats published openly:
1. Partially expert-calibrated demographic weights (±0.3-0.5pp impact)
2. Training set imbalanced toward Northern Italian regions
3. Coherence scores not validated on real campaign outcomes

---

## Production Status

**Deployment:** api2.polisim.dev (live since April 2026)  
**User Verticals:** 3 active (political movement, trade union, international NGO)  
**Validation:** Field testing in progress (May 2026)  
**Funding:** Applicant to [NGI Zero Commons Fund](https://nlnet.nl/commonsfund/) (code 2026-06-238)

**Proof of Concept Evolution:**
- PoC 1 (March 2026): OLS regression, 11 Lazio constituencies, RMSE 3.9pp
- PoC 2 (April 2026): Ridge regression, 142 national constituencies, RMSE 5.2pp
- PoC 3 (May 2026): Bayesian MRP, 220 constituencies, RMSE 4.3pp

---

## Recognition & Validation

**Civic Tech Directory:**
- Listed in [Civic Tech Guide](https://civictech.guide/) — Curated directory of recognized civic technology projects

**Research Network:**
- Practitioner contributor to mySociety/SITRA TICTeC Research (April-May 2026)
- Three technical reports submitted documenting PoC evolution and field validation

**Institutional Validation:**
- Field testing partnerships: Trade union confederation + International NGO (May 2026)
- Beta deployment in three user verticals (political movement, labor organization, nonprofit)

**Funding Pipeline:**
- NGI Zero Commons Fund applicant (code 2026-06-238)

---

## Architecture

┌──────────────────┐
│  Census Data     │  ISTAT 2021 (71M observations)
│  (ISTAT)         │
└────────┬─────────┘
│
▼
┌──────────────────┐       ┌─────────────────────┐
│  MRP Model       │◄──────│  Electoral Records  │  Eligendo OpenData
│  (PyMC)          │       │  (Eligendo)         │
└────────┬─────────┘       └─────────────────────┘
│                  ┌─────────────────────┐
├──────────────────│  Psychographic      │  ESS, Tripol, ITANES
│                  │  Segmentation       │
│                  └─────────────────────┘
▼
┌──────────────────┐       ┌─────────────────────┐
│  API Layer       │◄──────│  AI Provider        │  Claude/Mistral/LLaMA
│  (FastAPI)       │       │  (Substitutable)    │
└────────┬─────────┘       └─────────────────────┘
│
▼
┌──────────────────┐
│  Web Interface   │  api2.polisim.dev

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

**Preview production system:**
- Web interface: [polisim.dev](https://polisim.dev)
- API documentation: [api2.polisim.dev/docs](https://api2.polisim.dev/docs)
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

**Swap procedure** (conceptual, full implementation in Milestone 1):

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

This architecture ensures **no vendor lock-in** and supports future EU AI Act compliance with European foundation models.

---

## Retraining on Your National Data

PoliSim's architecture generalizes to any EU democracy with comparable open data infrastructure.

**Supported (or planned) electoral systems:**
- 🇮🇹 Italy (production): 220 constituencies, ISTAT + Eligendo + ITANES
- 🇫🇷 France (planned): circonscriptions législatives, INSEE data + ESS
- 🇩🇪 Germany (planned): Wahlkreise, Destatis data + ESS
- 🇪🇸 Spain (planned): circunscripciones, INE data + ESS
- 🇬🇧 UK (feasible): FPTP constituencies, ONS + Electoral Commission + ESS

**Conceptual retraining workflow:**

```bash
# 1. Prepare census data (CSV format)
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

# 5. Deploy with French model
python -m polisim.api --model models/model_FR.pkl --country FR
```

Full retraining documentation will be included in the Q3 2026 release.

---

## Documentation

**Live System:**
- [polisim.dev](https://polisim.dev) — Public web interface
- [api2.polisim.dev/docs](https://api2.polisim.dev/docs) — API reference (OpenAPI/Swagger)

**Methodology:**
- [Methodology (EN)](https://polisim.dev/en.html)
- [Metodologia (IT)](https://polisim.dev/metodologia.html)
- [Validation Results](https://polisim.dev/validazione)

**Research:**
- [PoliSim Overview (PDF)](https://polisim.dev/docs/polisim_overview_EN.pdf)
- mySociety/SITRA TICTeC Practitioner Reports (April-May 2026)

---

## Use Cases

### Trade Unions
Test whether a message on minimum wage or workers' rights is coherent with the union's statute and appropriate for the demographic reality of target regions.

### NGOs
Verify message-mission alignment before launching donor campaigns. Avoid strategic mistakes where message contradicts stated organizational values.

### Municipal Administrations
Evaluate citizen engagement strategies on census data rather than opaque commercial targeting tools like Facebook Ads.

### Political Movements
Q-Italia (qitalia.org) uses PoliSim as its live proof-of-concept: all content is generated with AI assistance, tested for coherence with 14 constitutional principles, and published only after mandatory human approval.

---

## Project Roadmap

### ✅ Completed (Q1-Q2 2026)
- Bayesian MRP production deployment
- Multi-step coherence evaluation pipeline
- ESS/Tripol psychographic integration
- Field validation partnerships (trade union + NGO)
- GDPR-compliant data anonymization tool (Data Shield)
- Public methodology documentation with declared limitations
- Listed in Civic Tech Guide directory

### 🚧 In Progress (Q2 2026)
- Open-source repository preparation
- AI provider abstraction layer (Mistral/LLaMA/Ollama support)
- Academic peer review coordination
- Field validation case study publication

### 📅 Planned (Q3-Q4 2026, subject to NGI funding)
- Full open-source release (MIT License)
- Shapefile-level MRP (spatial poststratification)
- Party-level model (individual parties vs coalition aggregates)
- EU electoral adapter (France + Germany proof-of-concept)
- Academic validation paper submission

---

## Funding & Sustainability

**Current Status:** Self-funded development (October 2025 - May 2026)

**Applied Funding:**
- [NGI Zero Commons Fund](https://nlnet.nl/commonsfund/) — Application code 2026-06-238 

**Sustainability Model:**
Post-grant, PoliSim will remain fully open-source (MIT License). The core infrastructure — MRP model, API, documentation — will always be free for self-hosting.

Optional professional support services may be offered for mission-critical institutional deployments (following the Red Hat model: free self-hosting, paid support for enterprise use cases).

---

## Contributing

**Full contribution guidelines** will be published with the Q3 2026 open-source release.

For now, if you are interested in:
- **Institutional validation partnerships** (NGOs, trade unions, municipalities)
- **Academic peer review** (statistics, political science, computational social science)
- **EU electoral adapter development** (France, Germany, Spain data expertise)

Please contact: **info@polisim.dev**

---

## License

**Full open-source release:** Q3 2026 under [MIT License](https://opensource.org/licenses/MIT)

**Current pre-release status:** Code is being prepared for public release. Repository structure, documentation, and test suite are works in progress aligned with NGI Zero Commons Fund milestones.

---

## Citation

If you reference PoliSim in academic work or policy documents, please cite:

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

**Web:** [polisim.dev](https://polisim.dev) | [qitalia.org](https://qitalia.org)  
**Email:** info@polisim.dev  
**NGI Application:** 2026-06-238  
**Civic Tech Guide:** [civictech.guide](https://civictech.guide/)

**Live Proof-of-Concept:** Q-Italia (qitalia.org) — Political movement with 14 constitutional principles using PoliSim infrastructure for values-anchored communication.

---

## Acknowledgments

**Data Sources:**
- ISTAT (Istituto Nazionale di Statistica) — Permanent Census 2021
- Ministry of Interior — Eligendo OpenData electoral results
- ITANES 2022 — Empirical voter segmentation (DOI: 10.13130/RD_UNIMI/JV77WR)
- European Social Survey (ESS) — Cross-national attitudinal data
- Tripol — Political psychology and value orientations dataset

**Research Network:**
- mySociety & SITRA — TICTeC civic tech research
- Civic Tech Guide — Recognized civic technology directory
- NGI Zero Commons Fund — Applied funding for open-source release

**AI Foundation:**
- Anthropic Claude — Current production AI provider
- Architecture designed for multi-provider support (Mistral, LLaMA, Phi, Ollama)

---

**Built with:** Python 3.11+ · PyMC · FastAPI · ISTAT OpenData · Eligendo OpenData · ESS · Tripol · Claude API

---

*Last updated: May 2026 · Version: Pre-release (open-source Q3 2026)*