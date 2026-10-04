# Preguntas posibles del profesor (y respuestas)

**1. ¿Por qué esto es un agente?**
Porque no sigue un flujo fijo: mantiene estado (cartera e historial), elige qué herramientas consultar según lo que necesite, razona sobre los resultados, actúa sobre un entorno (el broker simulado) y observa las consecuencias, que usa en el ciclo siguiente. El bucle está explícito en `agent/agent.py`.

**2. ¿Por qué no es simplemente un bot?**
Un bot tiene la lógica de decisión escrita de antemano (`si RSI < 30 comprar`). Acá la decisión la produce el modelo a partir de evidencia que puede ser contradictoria, y el código solo fija los límites. En el escenario «caída fuerte» el RSI sugiere sobreventa y las medias sugieren tendencia bajista: una regla fija tendría que elegir una; el agente pondera ambas y explica por qué. Además, el agente decide *qué* consultar.

**3. ¿Qué diferencia hay entre reglas e IA?**
Las reglas son explícitas, deterministas y auditables, pero no se adaptan al contexto. El LLM es flexible y explica su decisión, pero no es determinista ni garantiza corrección. Por eso usamos ambos: reglas para lo crítico (riesgo, saldo, ejecución) e IA para interpretar y decidir.

**4. ¿Qué herramientas utiliza el agente?**
`get_market_data`, `calculate_indicators`, `get_portfolio`, `calculate_risk`, `get_trade_history` (lectura) y `execute_virtual_order` (acción). Están en `agent/tools.py`.

**5. ¿Qué ocurre si el LLM se equivoca?**
Hay cinco barreras: esquema Pydantic, exigencia de haber consultado herramientas, verificación de las cifras citadas, gestión de riesgo y comprobaciones del broker. Si una rechaza, el agente recibe el motivo y tiene 2 intentos. Si no llega a una decisión válida, o la API falla, el sistema registra un HOLD de seguridad. Lo que no podemos verificar automáticamente es si su razonamiento es *bueno*: solo que sus cifras y acciones sean válidas.

**6. ¿Cómo controlan las alucinaciones?**
El prompt del ciclo no contiene precios ni saldos, así que solo pueden llegar por herramientas. El agente debe citar en `cited_values` las cifras en que se apoya y el sistema las compara con lo que devolvieron las herramientas en ese ciclo (con tolerancia de redondeo); si no coinciden, se rechaza. El precio de ejecución lo pone el sistema, nunca el modelo. Los tests lo cubren.

**7. ¿Cómo evitan operaciones inválidas?**
`RiskManager` valida saldo, posición, tope por operación (25 %), máximo de BTC, orden mínima y pérdida máxima acumulada; el broker repite saldo y posición. Existe además un stop-loss automático por código. Todo esto está cubierto por tests.

**8. ¿Por qué no utilizan Reinforcement Learning?**
RL necesita muchísimos episodios de entrenamiento, una función de recompensa bien diseñada y datos suficientes, y con una semana y recursos limitados corríamos el riesgo de sobreajustar a un histórico. Además el objetivo de la materia es mostrar el paradigma de agente basado en LLM con herramientas, y RL produce una política opaca, difícil de explicar. Es una evolución razonable como trabajo futuro, y una comparación interesante.

**9. ¿Qué limitaciones tiene el proyecto?**
El dataset de ejemplo es sintético; la simulación ejecuta al cierre con comisión fija y sin deslizamiento; un solo activo; los resultados de tramos cortos no son estadísticamente significativos; el LLM no es determinista; y no se validó ninguna estrategia. Las comparaciones con «comprar y mantener» son solo un control de cordura.

**10. ¿Cómo podría evolucionar en una versión futura?**
Más activos y gestión de portafolio, memoria de largo plazo y aprendizaje de errores pasados, evaluación sistemática (muchas corridas, varios modelos), un agente que proponga y otro que revise el riesgo, y comparar contra RL o contra estrategias clásicas.

---

## Preguntas extra que suelen aparecer

**¿Gana dinero?**
No lo afirmamos ni lo medimos como objetivo. Es una simulación educativa; los resultados dependen de un tramo corto y no validan ninguna estrategia. El resumen muestra la comparación con «comprar y mantener» para no sobrevalorarlos.

**¿Por qué datos sintéticos?**
Para que la demo sea reproducible: elegimos situaciones de mercado concretas (caída, rally, lateral, desplome). Se pueden usar velas reales con `scripts/download_history.py` en el backtest.

**¿Por qué el modelo llama a herramientas en lugar de recibir todo en el prompt?**
Para que los datos vengan del sistema (control de alucinaciones), para que el agente decida qué necesita (autonomía) y para poder auditar qué consultó.

**¿Cuántas llamadas al modelo consume?**
Lo esperable son 2 llamadas por ciclo (lecturas en paralelo y decisión); una orden rechazada suma una. En backtest un filtro de eventos evita consultar cuando no hay novedades, y el stop-loss no usa el modelo.

**¿Es reproducible?**
La situación de mercado sí (semilla fija y escenarios). La decisión del LLM puede variar; usamos temperatura baja para reducirlo y el historial guarda cada decisión.

**¿Y si el modelo obedece instrucciones maliciosas escondidas en los datos?**
Los datos de mercado son numéricos y provienen de nuestro proveedor; el modelo no lee texto libre externo. Además, aunque se equivocara, no puede saltarse el gestor de riesgo ni operar fuera del broker simulado.

**¿Qué demuestra el modo mock?**
Nada sobre IA: es un sustituto de reglas para probar el sistema sin red ni costo. Solo lo usamos en desarrollo y tests.
