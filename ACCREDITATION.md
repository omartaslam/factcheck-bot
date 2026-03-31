# Fred Fact Check — Accreditation & Verification Roadmap

> For review. Last updated: 2026-03-31.

---

## Recommended Order of Action

| Priority | Body | Effort | Timeline | Unlocks |
|---|---|---|---|---|
| 1 | **Google ClaimReview** | Low (technical) | 1–2 weeks | Search result visibility |
| 2 | **Duke Reporters' Lab** | Very low (form) | 1 day | Global census listing |
| 3 | **EFCSN** | High | 3–6 months | EU platform access, Meta EU |
| 4 | **IFCN** | High | 6–18 months | Gold standard, grants, Meta global |
| 5 | **Meta Third-Party** | Depends on IFCN/EFCSN | Post-accreditation | Facebook/Instagram/Threads labels |
| 6 | **JTI** | Medium | 3–6 months | Bing/algorithm integration |
| 7 | **The Trust Project** | Medium | 2–4 months | Transparency badge |

**The single biggest prerequisite for everything:** a public, browsable archive of Fred's fact-checks with stable URLs. IFCN, EFCSN, Google ClaimReview, and Meta all require it.

---

## 1. Google ClaimReview (Do First)

**What it is:** A structured data schema (JSON-LD) added to fact-check result pages. Google reads it and surfaces your verdicts directly in search results — no application or accreditation required.

**Requirements:**
- A public webpage for each fact-check with a stable URL
- ClaimReview JSON-LD markup on each page containing:
  - `claimReviewed` — the original claim
  - `reviewRating` — your verdict (TRUE / FALSE etc.) as a numeric value
  - `itemReviewed` — who made the claim and when
  - `author` — your organisation name and URL
  - `datePublished`
- Pages must be accessible to Googlebot (no login wall)

**Fred's status:** ❌ No public archive yet. This is the blocker.

**What to build:** A `/checks` page on fredcheck.com that lists published fact-checks, each with its own URL (`/checks/123`), ClaimReview markup, and the verdict. The data already exists in the `history` table in the DB.

**Benefit:** Immediate. Fred's verdicts appear in Google search results alongside claims.

**Apply at:** toolbox.google.com/factcheck/

---

## 2. Duke Reporters' Lab

**What it is:** The authoritative global census of fact-checking organisations, maintained by Duke University. ~440 organisations listed. Not accreditation — a registry — but being listed is a recognised credential and makes you findable by journalists, researchers, and platforms.

**Requirements:**
- Active, ongoing fact-checking output
- Public-facing website
- No editorial fee or application cost

**Fred's status:** ⚠️ Not listed. Can apply immediately once the public archive is live.

**Apply at:** reporterslab.org (contact form)

---

## 3. EFCSN — European Fact-Checking Standards Network

**What it is:** The European equivalent of IFCN. Established 2023. 61 verified members as of 2025. Required by Meta for the EU Third-Party Fact-Checking Programme. Backed by the European Commission.

**Why Fred should pursue this before IFCN:** Newer, more flexible on digital-native organisations, 2-year certification (vs IFCN annual), and Fred's audience is partly European/Spanish.

### The Five Areas Assessed

**1. Methodology**
- Transparent, documented process for selecting claims
- Multiple credible sources used
- Consistent evaluation regardless of claim origin
- Minimum 4 published fact-checks per month

**2. Ethics**
- Non-partisan — no political endorsements
- No partisan staff
- Conflict-of-interest disclosure
- Right of reply offered to fact-checked parties where feasible
- Prompt corrections with explanation

**3. Transparency**
- Ownership and governance disclosed
- Staff credentials published
- Funding sources >5% of revenue (or >€5,000) disclosed
- Editorial independence from funders confirmed

**4. Access**
- Public channel for claim submissions
- Corrections process publicly accessible

**5. Compliance**
- External assessment by two independent assessors
- Re-examination every 24 months

### Fred's Current Status Against EFCSN

| Requirement | Status | Notes |
|---|---|---|
| Methodology documented | ✅ | `/methodology` page drafted |
| Charter / bias principles | ✅ | Charter published at `/charter` |
| Corrections policy | ✅ | `/corrections` page drafted |
| Non-partisanship statement | ✅ | Covered in About page |
| Ownership disclosed | ✅ | `/about` page drafted |
| Funding disclosed | ✅ | Subscription revenue, no grants |
| Staff/editor named | ❌ | Named human editorial lead required |
| Public fact-check archive | ❌ | Must be built — biggest gap |
| Minimum 4 checks/month | ⚠️ | Dependent on user volume — need to demonstrate |
| Claim submission channel | ✅ | WhatsApp + web form |
| Right of reply process | ❌ | Not currently offered to fact-checked parties |
| AI disclosure | ✅ | Covered in About page |

### Application Process
1. Submit application at efcsn.com/application
2. Two independent assessors conduct blind evaluation
3. EFCSN Governance Body votes (⅔ majority required)
4. Certification issued for 24 months
5. **No application fee** (as of 2025)

---

## 4. IFCN — International Fact-Checking Network

**What it is:** The global gold standard for fact-checking accreditation. Administered by the Poynter Institute. 182 verified signatories in 57 countries. Unlocks the Meta Third-Party Fact-Checking Programme globally and the Google/IFCN Global Fact Check Fund ($25k–$100k grants).

**Application fee:** $350 USD
**Timeline:** 6–18 months
**Renewal:** Annual for first 5 years, then every 2 years

### The Five Principles — Full Requirements

**Principle 1: Non-Partisanship and Fairness**
- Fact-check using identical standards regardless of who made the claim
- Do not concentrate fact-checking on one political side
- Maintain editorial independence from political parties and advocacy groups
- Staff must refrain from political advocacy on matters being fact-checked
- _Fred's status:_ ✅ Charter covers this explicitly

**Principle 2: Standards and Transparency of Sources**
- Identify all significant sources in sufficient detail for independent verification
- Prioritise primary over secondary sources
- Cross-reference key claims against multiple named sources
- Disclose relevant interests of sources used
- _Fred's status:_ ⚠️ Sources listed in verdicts but depth varies — needs improvement

**Principle 3: Transparency of Funding & Organisation**
- Disclose all funding sources accounting for ≥5% of revenue
- Clarify legal status and ownership structure
- Explain how editorial control operates
- Provide professional biographies of key staff
- Provide accessible communication channels
- _Fred's status:_ ✅ `/about` page covers this — staff bios still needed

**Principle 4: Standards and Transparency of Methodology**
- Publish claim selection, research, and publication processes
- Base claim selection on reach and public importance
- Present evidence both supporting and undermining claims
- Apply consistent assessment standards
- Attempt to contact claim-makers where feasible
- Encourage public submissions
- _Fred's status:_ ✅ `/methodology` page covers this

**Principle 5: Open and Honest Corrections Policy**
- Maintain an accessible corrections policy
- Handle mistakes transparently and openly
- Ensure corrections reach the same audience as the original
- Inform users of IFCN complaint procedures
- _Fred's status:_ ✅ `/corrections` page covers this; IFCN complaint reference added

### IFCN Assessment — 31 Criteria

IFCN assessors evaluate against 31 specific criteria across the five principles. Key ones Fred needs to prepare for:

- Evidence that fact-checks are published with stable URLs
- Demonstration of consistent methodology across multiple published checks
- Named editor/publisher with contactable details
- Evidence of at least one published correction
- Proof of editorial independence from funders
- Active claim submission channel
- Demonstrated volume of published output

### Fred's Current Status Against IFCN

| Requirement | Status | Notes |
|---|---|---|
| Non-partisanship documented | ✅ | Charter + About page |
| Source transparency in verdicts | ⚠️ | Needs consistent source citation in public checks |
| Funding transparency | ✅ | About page |
| Staff bios | ❌ | Named human editor required |
| Methodology published | ✅ | Methodology page |
| Corrections policy | ✅ | Corrections page |
| Public fact-check archive | ❌ | Must be built — primary blocker |
| Demonstrated output volume | ❌ | Need 3–6 months of published checks |
| At least one published correction | ❌ | Need at least one on record |
| Contact for claim-maker | ❌ | Right of reply process not yet built |
| IFCN complaint procedure noted | ✅ | Added to corrections page |

### Application Process
1. Register at ifcncodeofprinciples.poynter.org
2. Submit application with supporting documentation
3. Pay $350 fee
4. Independent external assessor evaluates against 31 criteria (blind)
5. Decision issued — approved, approved with conditions, or rejected with feedback
6. Annual renewal
- **Apply at:** ifcncodeofprinciples.poynter.org

---

## 5. Meta Third-Party Fact-Checking Programme

**What it is:** Meta (Facebook, Instagram, Threads) partners with accredited fact-checkers to label misinformation on the platform. Content identified as false is labelled and has reduced distribution.

**Requirements:**
- Outside USA: Must hold IFCN or (in Europe) EFCSN certification
- Independent from Meta
- Adhere to journalism quality and transparency standards
- Active, ongoing output

**Status (2025):** US programme ended January 2025 (replaced by Community Notes). International programme continues. Some uncertainty about long-term direction.

**Fred's status:** ❌ Blocked on IFCN/EFCSN — pursue after accreditation.

**Apply at:** facebook.com/journalismproject/programs/third-party-fact-checking

---

## 6. JTI — Journalism Trust Initiative (Reporters Without Borders)

**What it is:** An ISO-aligned standard for journalism trustworthiness, developed by RSF (Reporters Without Borders). 2,000+ outlets registered in 119 countries. Integrated into Bing search algorithm, Cafeyn (news aggregator), and EBU's YEP programme.

**Distinct from IFCN/EFCSN:** JTI certifies journalistic *processes and transparency* across the whole organisation — not individual fact-checks. Treats Fred as a media outlet, not just a fact-checker. Complementary, not competing.

**Requirements (130 criteria across 4 areas):**
1. Editorial processes and transparency
2. Ownership and independence disclosure
3. Journalistic ethics compliance
4. Professional practices

**Process:**
1. Self-evaluation (answer 130 questions)
2. Publish transparency report (optional but recommended)
3. External audit by accredited firm
4. Certification issued

**No application fee** for self-evaluation phase.

**Fred's status:** ⚠️ Most requirements overlap with IFCN/EFCSN prep. Pursue after EFCSN.

**Apply at:** journalismtrustinitiative.org

---

## 7. The Trust Project

**What it is:** An international consortium of 120+ news organisations committed to transparency standards. Members include The Economist, El País, Washington Post, CBC News, Corriere della Sera. Grants a Trust Mark displayed on published content.

**Requirements (Trust Indicators):**
- Label content by type (news, opinion, fact-check, sponsored)
- Disclose ownership and structure
- Explain journalist/author expertise
- Detail editorial standards and corrections policy
- Transparency on funding and sponsorships
- Author accountability and contact information

**Fred's status:** ⚠️ Most indicators already addressed by existing pages. Worth pursuing — relatively low barrier, recognised by major publishers.

**Apply at:** thetrustproject.org

---

## Gaps Summary — What Needs to Be Built

### Must-do before applying anywhere

1. **Public fact-check archive** (`/checks`)
   - Browsable list of published fact-checks with stable URLs
   - Each check needs: claim, verdict, sources, date, confidence
   - Data is already in the DB — needs a frontend
   - Unlocks Google ClaimReview, IFCN, EFCSN, Meta, Duke

2. **Named human editorial lead**
   - A named person (not "Fred Check") with bio and contact details
   - Required by IFCN (Principle 3), EFCSN, and JTI
   - Can be brief — name, role, professional background

3. **ClaimReview JSON-LD markup**
   - Add to each public fact-check URL
   - Immediate Google search visibility
   - Technical task: ~1 day to implement once archive exists

### Should-do before EFCSN/IFCN application

4. **Right of reply process**
   - When a fact-check names a specific person or organisation, offer them the opportunity to comment before publication
   - Both EFCSN and IFCN assess for this
   - Simple process: email to named party, 48-hour window, response appended to verdict

5. **Demonstrated output volume**
   - 3–6 months of published, public fact-checks
   - IFCN assessors want to see consistent methodology applied across real checks
   - EFCSN minimum: 4 published checks per month

6. **At least one published correction on record**
   - IFCN explicitly checks for this
   - If no errors have been made and corrected yet, this will be a gap at application time

### Nice-to-have

7. **Staff page** — named editor + any contributors, with brief bios
8. **Annual report / transparency report** — funding, output volume, corrections — JTI and Trust Project look for this
9. **Claim submission form** — public-facing, separate from the main fact-check tool, where anyone can submit a claim for checking

---

## Quick Reference — All Links

| Body | URL |
|---|---|
| IFCN application | ifcncodeofprinciples.poynter.org |
| EFCSN application | efcsn.com/application |
| Google ClaimReview | toolbox.google.com/factcheck |
| Google ClaimReview docs | developers.google.com/search/docs/appearance/structured-data/factcheck |
| Duke Reporters' Lab | reporterslab.org |
| Meta Fact-Checking Programme | facebook.com/journalismproject/programs/third-party-fact-checking |
| JTI | journalismtrustinitiative.org |
| The Trust Project | thetrustproject.org |
| EDMO (Europe) | edmo.eu |

---

## Fred's Pages — Current State

| Page | URL | Status |
|---|---|---|
| About & Transparency | /about | ✅ Drafted — needs staff names |
| Methodology | /methodology | ✅ Drafted — references archive (not built yet) |
| Corrections | /corrections | ✅ Drafted — includes IFCN complaint reference |
| Charter | /charter | ✅ Live |
| Fact-check archive | /checks | ❌ Not built — primary blocker |
