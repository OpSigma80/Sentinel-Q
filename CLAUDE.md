# Sentinel-Q — Agent Configuration

## 🤖 Agent Hierarchy

### Opus (Orchestrator)
Úsalo para:
- Diseño de arquitectura y decisiones técnicas críticas
- Análisis de deuda técnica
- Planificación de semana/sprint
- Refactors complejos con múltiples archivos
- Decisiones de seguridad (JWT, multi-tenant, encrypted logs)
- Code review final antes de merge

### Sonnet (Main Executor)
Úsalo para:
- Implementación de features del roadmap
- Debugging intermedio
- Integración entre módulos
- Edits que afectan lógica de negocio
- Documentación técnica

### Haiku (Fast Worker)
Úsalo para:
- Type hints y pequeñas correcciones
- Tests unitarios repetitivos
- Rename, formateo, lint fixes
- Búsqueda rápida en el código
- Preguntas simples sobre el stack

---

## 📋 Regla de oro
> Antes de ejecutar cualquier cambio:
> 1. Revisa el estado actual del código afectado
> 2. Identifica deuda técnica relacionada
> 3. Propón el approach y espera aprobación
> 4. Implementa solo lo acordado
> 5. Sé crítico y directo — sin filtros, sin suavizar

---

## 🏗️ Stack
- **Backend:** FastAPI + PostgreSQL + APScheduler + Telegram Bot
- **Auth (próximo):** JWT + Multi-tenant
- **Infra:** iMac 2009 — CPU severamente limitado

## ⚠️ Restricciones de Hardware (NO NEGOCIABLES)
- **Prohibido:** numpy, pandas, streamlit, cualquier lib pesada
- **Máximo overhead por feature:** 2-3% CPU
- **Prioridad:** librerías ligeras, código eficiente, lazy loading
- Antes de sugerir cualquier dependencia nueva: verificar peso y CPU impact

## ✅ Estado Actual — v1.0 Production-Ready
Funcionando correctamente:
- Health scores
- Graceful shutdown
- Encrypted logs
- Security hardened

## 🗺️ Roadmap Activo

| Semana | Objetivo | Estado |
|--------|----------|--------|
| 1 | Telegram mejorado + Alertas inteligentes | 🔄 En curso |
| 2 | JWT Auth + Multi-tenant + Admin panel | ⏳ Pendiente |
| 3 | Rate limiting + Webhooks outbound | ⏳ Pendiente |
| 4 | Documentación + Postman + Polish | ⏳ Pendiente |

## 📐 Estándares Obligatorios (sin excepciones)
- [ ] Type hints 100% en todo código nuevo
- [ ] Unit tests obligatorios por feature
- [ ] Code review antes de cualquier merge
- [ ] Documentación professional-grade
- [ ] Zero shortcuts en producción
- [ ] Production-ready desde el primer commit

---

## 🧠 Cómo pensar en este proyecto
Piensa como **architect senior**, no como junior.
- Anticipa problemas antes de que ocurran
- Prioriza estabilidad sobre velocidad de entrega
- El hardware es el constraint principal — respétalo siempre
- Este proyecto evoluciona hacia SaaS profesional — cada decisión debe escalar
