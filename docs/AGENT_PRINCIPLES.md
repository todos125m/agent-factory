# اصول Agent — شش Engine

> منبع: یادداشت دست‌نویس صاحب پروژه. این سند مکمل `ARCHITECTURE.md` است:
> ARCHITECTURE می‌گوید تیم Agentها چطور سازمان‌دهی می‌شوند؛ این سند می‌گوید **خودِ یک Agent از درون چطور کار می‌کند.**

```text
                 6. Synthesis
                      ▲
   1. Persona ──┐     │
                ├── AGENT ──→ 3. Action ──→ 4. Observation
   2. Knowledge ┘     │
      Boundary        ▼
                 5. Memory / Log
```

---

## 1. Persona Engine
تعیین می‌کند Agent **کیست**:
- چه می‌داند
- چقدر صبر دارد
- چه چیزی را نمی‌فهمد

> ⚠️ **نباید همه‌چیزدان شود.**

## 2. Knowledge Boundary
Agent فقط اجازه دارد از این‌ها استفاده کند:
- آنچه **یاد گرفته**
- **در همان فاز**
- **با همان Persona**

> ⚠️ **نه دانش کلی LLM.**
> ⚠️ **نه بهترین تجربه‌ی دنیا.**

## 3. Action Engine
Agent **کار می‌کند**:
- کلیک می‌کند
- انتخاب می‌کند
- مسیر را رها می‌کند

Action باید:
- **هدف‌دار** باشد
- **ناقص** باشد
- **انسانی** باشد

## 4. Observation Engine
هر عمل ← یک واکنش. واکنش‌ها سه نوع‌اند:
- ذهنی
- احساسی
- رفتاری

> Agent باید **حس کند**، نه فقط ثبت کند.

## 5. Memory / Log
Agent تجربه‌ها را یادداشت می‌کند. دو نوع حافظه:
- **Memory کوتاه‌مدت** — همین‌جا، همین جلسه
- **Memory تحلیلی** — آرشیوها

> ⚠️ حافظه ≠ یادگیری آزاد. *(خوانش از دست‌نوشته قطعی نیست — تأیید شود)*

## 6. Synthesis Engine
در پایان، Agent:
- داده‌ها را می‌بیند
- الگو می‌سازد
- Insight استخراج می‌کند

**ولی:**
- عجله نمی‌کند
- نتیجه‌گیری را **معلق** نگه می‌دارد

---

## ارتباط با ARCHITECTURE.md

| Engine | جای آن در معماری | پیاده‌سازی |
|---|---|---|
| Persona | Agent Registry (§10) — فیلد `persona` کنار `capabilities` | فاز ۳ |
| Knowledge Boundary | Context Builder (§25) — فقط memory مجاز همان فاز/Persona به مدل می‌رسد؛ system prompt صریحاً استفاده از دانش عمومی را منع می‌کند | فاز ۳ + ۷ |
| Action | Tool Layer (§27) — ابزارهای permissioned؛ برای Agent شبیه‌ساز کاربر: click / choose / abandon | فاز ۴ |
| Observation | RunEvent + Evidence (§48) — هر action یک event با `reaction: {cognitive, emotional, behavioral}` | فاز ۴ |
| Memory / Log | Shared Memory (§24) — short-term state + long-term project memory | فاز ۷ |
| Synthesis | Evidence Model (§48) — خروجی به‌صورت FACT / INFERENCE / HYPOTHESIS با `confidence`؛ نتیجه‌ی قطعی فقط با شواهد کافی | فاز ۴ |

### پیش‌نویس Persona spec

```yaml
persona:
  name: first_time_freelancer
  knows: [basic_web_apps, telegram]
  does_not_understand: [saas_pricing_tiers, api_keys]
  patience: low            # low | medium | high
  goals: [find_cheap_tool_fast]
knowledge_boundary:
  allowed_sources: [persona.knows, memory.phase_current]
  general_llm_knowledge: false
action_style:
  purposeful: true
  imperfect: true          # may misclick, skip, abandon
  human: true
observation:
  record: [cognitive, emotional, behavioral]
synthesis:
  suspend_conclusion: true
  min_evidence_before_claim: 3
```
