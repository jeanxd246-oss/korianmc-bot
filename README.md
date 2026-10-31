# 🤖 KorianMC Bot

Bot premium para comunidades **gaming / Minecraft**, escrito en **Python 3.11+** con
[discord.py 2.x](https://discord.py.readthedocs.io/). Todo en español, con comandos
**slash `/`** y de **prefijo `!` / `/`**, base de datos **SQLite** auto-creada y un
sistema de IA propio: **KorianMC IA** (Gemini).

Estilo visual: premium, oscuro y tecnológico. Color principal de los embeds: **#5865F2**
(configurable).

---

## 1. Requisitos

| Requisito | Detalle |
|-----------|---------|
| Python | **3.11 o superior** (probado con 3.11 – 3.14) |
| Cuenta Discord | [Discord Developer Portal](https://discord.com/developers/applications) |
| Cuenta Google (opcional) | Clave gratuita de [Google AI Studio](https://aistudio.google.com/apikey) para la IA |
| Node.js (opcional) | 18+ sólo si quieres usar el panel web admin |
| Discord | Permiso para invitar el bot con los intents **Server Members** y **Message Content** |

---

## 2. Instalación de dependencias

```bash
cd KorianMCBot
python -m venv venv
source venv/bin/activate        # Linux/macOS
# venv\Scripts\activate         # Windows

pip install -r requirements.txt
```

Dependencias fijadas en `requirements.txt`:

```
discord.py==2.4.0
aiosqlite==0.20.0
python-dotenv==1.0.1
google-generativeai==0.8.6
APScheduler==3.10.4
aiohttp==3.14.3
```

---

## 3. Crear el bot en Discord Developer Portal

1. Entra a <https://discord.com/developers/applications> → **New Application**.
2. Pestaña **Bot** → **Add Bot**.
3. Activa:
   - **Privileged Gateway Intents → SERVER MEMBERS INTENT** (Server Members)
   - **Privileged Gateway Intents → MESSAGE CONTENT INTENT** (Message Content)
   - **Privileged Gateway Intents → PRESENCE INTENT** (opcional)
4. En **OAuth2 → URL Generator** marca los scopes `bot` y `applications.commands` y
   estos permisos: `Manage Channels`, `Manage Roles`, `Manage Messages`, `Moderate Members`,
   `Ban Members`, `Kick Members`, `Send Messages`, `Embed Links`, `Attach Files`,
   `Read Message History`, `Add Reactions`, `Manage Nicknames` (o simplemente *Administrator*
   para una prueba rápida).
5. Abre la URL generada e invita el bot a tu servidor.
6. Pestaña **Bot** → **Token** → **Reset Token** y **copia el token**. ⚠️ **Nunca lo compartas.**

> Si tu bot va a moderar con IA o niveles, el intent **Message Content** es obligatorio.

---

## 4. Configurar el archivo `.env`

Copia la plantilla y edita tus valores:

```bash
cp .env.example .env
```

```env
DISCORD_TOKEN=PEGA_AQUI_TU_TOKEN      # ← el token del Developer Portal
GEMINI_API_KEY=                        # ← opcional, clave de Google AI Studio
GEMINI_MODEL=gemini-1.5-flash
BOT_COLOR=#5865F2
BACKUP_INTERVAL_HOURS=6
```

Reglas de seguridad:

- El archivo `.env` está en `.gitignore`: **nunca** se sube a Git ni se envía a nadie.
- El bot **nunca** imprime ni envía el token por Discord.
- Si pierdes el token, haz *Reset Token* en el Developer Portal.
- Si falta `GEMINI_API_KEY`, **el bot arranca igual**: los módulos de IA se desactivan solos
  y muestran un aviso elegante.

---

## 5. Arrancar el bot

```bash
python main.py
```

Verás algo como:

```
INFO | database | Base de datos lista en .../data/korianmc.db
INFO | bot | 45 comandos slash sincronizados.
INFO | bot | Conectado como KorianMC Bot#1234 (ID ...) en 1 servidor(es).
```

La base de datos `data/korianmc.db` **se crea sola** en el primer arranque: no hace falta
importar nada ni tocar SQL.

Para detenerlo: `Ctrl + C`.

---

## 6. Guía paso a paso de `/setup all`

Con **un solo comando** quedan configurados canales, roles y paneles:

1. Ve a un canal de texto del servidor (se usará como canal de logs, tickets, niveles,
   puntos, sugerencias, encuestas, sorteos, reportes, postulaciones, moderación,
   verificación e IA).
2. Ejecuta:

   ```
   /setup all
   ```

   Opcionalmente especifica canales distintos:

   ```
   /setup all canal:#staff bienvenida:#bienvenidas categoria_tickets:Categoría Tickets
   ```

3. El bot publica:
   - 🎫 **Panel de tickets** con select de 7 categorías (en la categoría de tickets si indicaste una).
   - ✅ **Panel de verificación** con botón.
   - Los **10 roles de nivel** (`Level 1` … `Level Demon`) con colores distintos.
4. **Muy importante:** coloca el rol del bot **por encima** de los roles de nivel
   (Ajustes de servidor → Roles) o no podrá asignarlos.
5. Revisa cada sistema con sus comandos individuales:

   | Paso | Comando |
   |------|---------|
   | Canales | `/setup logs #logs` · `/setup welcome #bienvenidas` · `/setup tickets #tickets` |
   | Niveles | `/setup levels #niveles` · `/setup roles` |
   | Moderación | `/setup moderation canal_reportes:#reportes` |
   | Comunidad | `/setup suggestions` · `/setup polls` · `/setup giveaways` |
   | Verificación | `/setup verification canal:#verify rol:@Verificado` |
   | Reacción-rol | `/reactionroles crear rol:@Rol emoji:🎉` |
   | IA | `/iaconfig canal:#soporte personalidad:cercano, breve y profesional` |
   | Plantillas DM | `/plantillas set tipo:welcome texto:¡Bienvenido {user}!` |

Los IDs por defecto que trae `config.json` (logs `1560096028546564127`,
bienvenida `1552370341895077918` y roles de staff `1562252845124820992`,
`1560004989408247848`) pueden sobrescribirse en cualquier momento con los mismos
comandos de `/setup`.

---

## 7. Tabla de comandos disponibles

Todos funcionan como **slash `/`** y como **prefijo** (`!comando` o `/comando` escrito en chat).

### Configuración y utilidad
| Comando | Descripción | Permisos |
|---------|-------------|----------|
| `/setup tickets\|welcome\|levels\|moderation\|economy\|suggestions\|polls\|giveaways\|verification\|reactionroles\|roles\|logs\|all` | Configura cada sistema | Admin |
| `/plantillas set\|list` | Plantillas de DM automáticas | Admin |
| `/setinfo ip web descripcion color` | Información pública del servidor | Admin |
| `/ip [nueva_ip]` | Muestra/cambia la IP del servidor | Público (cambio: Admin) |
| `/backup create\|list\|restore` | Copias de seguridad SQLite | Admin |
| `/help [sección]` · `/ping` · `/botinfo` · `/serverinfo` | Ayuda e información | Público |
| `/logs [cantidad]` | Últimos eventos registrados | Staff |

### Soporte
| Comando | Descripción | Permisos |
|---------|-------------|----------|
| `/ticket create [categoria] [razon]` | Abre un ticket (modal de razón obligatoria) | Público |
| `/ticket close [razon]` · `🔒` | Cierra con confirmación y borrado automático | Creador o Staff |
| `/ticket transcript` · `📄` | Genera el `.html` y lo envía a logs | Creador o Staff |
| `/ticket claim` · `🤝` | Reclama un ticket | Staff |
| `/report send @usuario` | Formulario de reporte (modal) | Público |
| `/report list [estado]` | Lista reportes | Staff |
| `/report history [@usuario]` | Historial | Público (otros: Staff) |
| `/faq add\|remove\|list\|buscar` | Preguntas frecuentes (alimentan la IA) | `add/remove` Admin |
| `/postulacion` · `/postulacion puestos` · `/postulacion list` | Select + modal por puesto | Público / Staff |
| `/suggest send\|list\|history` | Sugerencias con votos 👍/👎 | Público |

### Comunidad
| Comando | Descripción | Permisos |
|---------|-------------|----------|
| `/level view\|top` · `/points view\|top\|pay\|add\|remove` | Niveles y puntos | `add/remove` Staff |
| `/poll create\|end\|list` | Encuestas con botones | `end` Staff |
| `/giveaway create\|end\|reroll\|list\|info` | Sorteos | `end/reroll/list` Staff |
| `/reactionroles crear\|eliminar\|lista` | Roles por reacción | Staff |
| `/afk [razón]` · `/afk-list` | Estado de ausente | Público |
| `/remind <duración> <texto>` · `/remind-list` · `/remind-cancel` | Recordatorios | Público |
| `/referir [canjear codigo] [top]` | Códigos de referido | Público |

### Moderación
| Comando | Descripción | Permisos |
|---------|-------------|----------|
| `/ban @usuario [razón] [días]` | Baneo con confirmación | Staff |
| `/tempban @usuario <duración> [razón]` | Baneo temporal (se levanta solo) | Staff |
| `/kick @usuario [razón]` | Expulsión | Staff |
| `/timeout @usuario <duración> [razón]` · `/unmute @usuario` | Silenciar / liberar | Staff |
| `/warn add\|list\|clear` · `/warn-historial @usuario` | Advertencias e historial | Staff |
| `/automod` · `/automod-estado` | Prueba y estado del automod | Staff |
| `/raidmode on\|off\|verify` · `/raidstatus` | Anti-raid | Admin / Staff |
| `/iamod estado\|on\|off\|umbral\|log` | Moderación con IA | Admin |

### Economía
| Comando | Descripción | Permisos |
|---------|-------------|----------|
| `/tienda` | Productos y últimas compras | Público |
| `/buy <producto> [puntos\|stripe\|paypal]` | Compra con confirmación | Público |
| `/product create\|list\|edit\|set-price\|delete` | Gestión de productos | Admin |
| `/product entregar <id>` | Entrega manual de una compra | Staff |
| `/pagos` | Estado de los pagos externos | Admin |

### IA — KorianMC IA
| Comando | Descripción | Permisos |
|---------|-------------|----------|
| `/ia chat <texto>` | Conversa con la IA (con contexto) | Público |
| `/ia resumir <texto\|url>` | Resume texto o una web | Público |
| `/ia traducir <texto> [idioma]` | Traductor | Público |
| `/ia imagen <descripción>` | Genera una imagen | Público |
| `/ia moderar <texto>` | Analiza toxicidad y spam | Público |
| `/ia soporte <pregunta>` | Dudas frecuentes (FAQ + IA) | Público |
| `/iaconfig [canal] [personalidad] [cooldown] [cuota]` · `/iaconfig reset @usuario` | Configuración | Admin |

---

## 8. Permisos (qué rol puede usar qué)

Los permisos se comprueban **por ID de rol**, nunca por nombre (así renombrar roles no
rompe nada). Configúralos en `config.json` o directamente en la tabla `guild_config`.

| Nivel | Quién | Comandos |
|-------|-------|----------|
| **Administrador** | Permiso *Administrator* del servidor o rol en `roles.admin` | `/setup`, `/backup`, `/setinfo`, `/product`, `/iaconfig`, `/iamod`, `/plantillas`, `/pagos`, `/ip <nueva>` |
| **Staff** | Rol en `roles.staff` (por defecto `1562252845124820992` y `1560004989408247848`), o permisos de gestión del servidor | cerrar tickets ajenos, reportes, postulaciones, aprobar/rechazar sugerencias, sorteos, moderación, `/logs` |
| **Usuario** | Cualquier miembro | comandos públicos: `/tienda`, `/level`, `/points`, `/afk`, `/ia`, `/help`, etc. |

Además, el bot necesita permisos del **rol del bot**: gestionar canales, roles, mensajes,
moderar miembros, banear y expulsar, y estar **por encima** de los roles que asigna.

---

## 9. Configurar Gemini (IA)

1. Entra en <https://aistudio.google.com/apikey> y crea una clave **gratuita**.
2. Pégala en el `.env`:

   ```env
   GEMINI_API_KEY=tu_clave_aqui
   GEMINI_MODEL=gemini-1.5-flash
   ```

3. Reinicia el bot (`/ping` muestra `IA: ✅`).

**Cuotas:** el nivel gratuito de Gemini incluye un límite diario de solicitudes y de
tokens por minuto. El bot ya respeta *rate limits*:

- Llamadas con **timeout** (por defecto 30 s) y ejecución en `asyncio.to_thread`
  (nunca bloquea el event loop).
- **Cooldown por usuario** (20 s por defecto), **anti-duplicado** (120 s) y
  **cuota diaria** (30 mensajes/usuario) configurables con `/iaconfig`.
- Si la API falla o no hay clave, el bot responde con un aviso elegante y **sigue
  funcionando el resto de módulos**.

**Qué hace KorianMC IA:** escucha el canal de soporte configurado
(`/iaconfig canal:#soporte`), clasifica la intención del mensaje
(`PREGUNTA`, `QUEJA`, `SPAM`, `NO_APLICA`), responde con la base de conocimiento del
servidor, mantiene el contexto de los últimos N mensajes y ofrece el botón
**«Escalar a humano»** para avisar al staff. Las respuestas a `QUEJA` invitan siempre a
escalar. La personalidad por defecto es *cercana, breve y profesional*.

---

## 10. Cómo funciona el sistema de tickets

```
Usuario selecciona categoría en el panel (Select Menu)
        │
        ▼
MODAL «Razón del ticket:»  ← OBLIGATORIO (mínimo 8 caracteres)
        │  sin razón → NO se crea el ticket
        ▼
Canal privado  ticket-usuario
  · @everyone no ve el canal
  · solo el creador y los roles de staff lo ven
  · Embed inicial: usuario, categoría, RAZÓN y fecha
  · Botones: 🔒 Cerrar · 🔔 Avisar staff · 📄 Transcripción · 🗑️ Eliminar
        │
        ▼  🔒 (o /ticket close)
MODAL «Razón del cierre» (opcional)
        │
        ▼
BOTÓN DE CONFIRMACIÓN «Cerrar y eliminar»
        │  evita cierres accidentales
        ▼
1. Se guarda quién cerró, fecha y motivo en `tickets`
2. Se genera la transcripción .html en `data/transcripts/`
3. Se envía al canal de logs (adjunto)
4. Se envía una DM al usuario con el archivo y el enlace al log
5. SE ELIMINA EL CANAL AUTOMÁTICAMENTE
```

Protecciones incluidas:

- **1 ticket abierto por usuario** a la vez.
- **Cooldown anti-spam** entre tickets (120 s, configurable).
- Solo el creador o el **staff** pueden cerrar tickets ajenos.
- Errores de permisos al crear/borrar canales → mensaje claro, sin tracebacks.
- Todos los mensajes del canal se guardan en `ticket_messages` para la transcripción
  (con enmascaramiento de tokens, correos e IPs).

---

## 11. Backups: crear y restaurar

```bash
/backup create    # copia manual → data/backups/manual_AAAAMMDD_HHMMSS.db
/backup list      # lista las copias con tamaño y fecha
/backup restore id:3   # restaura la copia #3 (con confirmación por botón)
```

- Copia automática cada **`BACKUP_INTERVAL_HOURS`** horas (6 por defecto; `0` la desactiva),
  con poda automática: se conservan las 20 últimas copias automáticas.
- La restauración usa el mecanismo de *backup* de SQLite: cierra la base, la sustituye y
  la vuelve a abrir. El bot sigue en marcha y ya lee los datos restaurados.
- **Recomendado:** haz `/backup create` antes de operaciones sensibles (restaurar,
  migrar el bot a otro servidor).

---

## 12. Productos, niveles, roles y canales

### Añadir productos
```
/product create nombre:"Rango VIP" precio:500 descripcion:"Acceso al rango durante 30 días" stock:10 rol:@VIP
/product set-price producto:"Rango VIP" precio:750
/product edit producto:"Rango VIP" descripcion:"Nueva descripción"
/product list
/product delete producto:"Rango VIP"
```
El usuario compra con `/buy Rango VIP` (confirmación por botón). El rol se asigna
automáticamente si se indicó; si no, el staff la entrega con `/product entregar <id>`.
Los pagos con Stripe/PayPal están **preparados pero desactivados** hasta definir
`PAYMENTS_PROVIDER` y las claves en `.env` (`/pagos` muestra el estado).

### Niveles y roles
- `/setup roles` crea `Level 1` … `Level 9` y `Level Demon` (nivel 10) con colores distintos
  y los guarda en la tabla `level_roles`.
- Al subir: se **quita el rol anterior** y se da el nuevo, y se anuncia en el canal de
  niveles con embed (usuario, nuevo nivel, rol y XP).
- Anti-spam de XP: cooldown entre mensajes, mensajes muy cortos ignorados, mensajes
  repetidos ignorados, detección de *flood* y **diminishing returns**; los mensajes largos
  y coherentes dan más XP.
- `/level view` muestra barra de progreso; `/level top` lista el ranking paginado.

### Cambiar canales y colores
```
/setup logs canal:#logs
/setinfo ip:play.ejemplo.net web:https://ejemplo.net color:#5865F2
/iaconfig canal:#soporte personalidad:cercano y breve cooldown:15
```
También puedes editar `config.json` (valores por defecto) antes de arrancar.

---

## 13. Panel web admin (Express, opcional)

Panel **solo lectura** con autenticación por token.

```bash
# 1) en .env
PANEL_ENABLED=true          # hace que el bot exporte data/dashboard.json cada minuto
PANEL_PORT=3000
PANEL_TOKEN=un_token_largo_y_secreto

# 2) arrancar el panel
cd web
npm install
npm start                   # http://127.0.0.1:3000
```

El panel muestra recuentos (tickets, reportes, sorteos, errores…), top de puntos,
últimos eventos y últimos errores técnicos. Sin `PANEL_TOKEN` definido, no arranca.

---

## 14. Estructura del proyecto

```
KorianMCBot/
├── main.py                 # punto de entrada
├── requirements.txt
├── .env.example
├── .gitignore
├── config.json             # valores por defecto (canales, roles, categorías…)
├── README.md
├── core/                   # bot, config, base de datos, errores, eventos, logs
├── cogs/                   # 22 módulos (tickets, niveles, IA, moderación…)
├── services/               # transcripciones, embeds, IA, planificador, sorteos
├── utils/                  # embeds, permisos, validación, tiempo, anonimización
├── web/                    # panel Express opcional
└── data/                   # korianmc.db, transcripts/, backups/ (se crean solos)
```

Datos generados (nunca se suben a Git): `data/*.db`, `data/backups/*`,
`data/transcripts/*.html`, `data/bot.log`, `data/dashboard.json`.

---

## 15. Troubleshooting

| Síntoma | Causa probable | Solución |
|---------|----------------|----------|
| `No se encontró DISCORD_TOKEN` | Falta el `.env` | `cp .env.example .env` y pega el token; reinicia |
| `LoginFailure: Improper token has been passed` | Token incorrecto o reseteado | Copia de nuevo el token en el Developer Portal |
| El bot entra pero no responde a `/comando` | Faltan scopes o intents | Re-invita con `bot` + `applications.commands`; activa *Message Content* y *Server Members* |
| Los comandos slash no aparecen | Sincronización global de Discord (hasta 1 h) | Reinicia el bot y espera; comprueba `45 comandos slash sincronizados` en el log |
| `PrivilegedIntentsRequired` | Intent desactivado | Activa los intents en la pestaña **Bot** |
| No asigna roles de nivel | Rol del bot por debajo | Sube el rol del bot por encima de `Level 1 … Level Demon` |
| No puede crear/borrar tickets | Faltan permisos | *Manage Channels* + *Manage Roles* + bot arriba de `@everyone` |
| `IA no disponible: no hay GEMINI_API_KEY` | Sin clave en `.env` | Añade `GEMINI_API_KEY` y reinicia (el resto del bot sigue funcionando) |
| La IA responde lento o con error 429 | Cuota de Gemini agotada | Espera, cambia de modelo en `GEMINI_MODEL` o reduce `/iaconfig cuota:10` |
| `/backup restore` dice que falta el archivo | Copia borrada de `data/backups` | Usa otra copia de `/backup list` |
| Errores con `sqlite3.OperatoryError: database is locked` | Varias instancias del bot | Ejecuta **una sola** copia de `python main.py` |
| El panel web no muestra datos | El bot no exporta | `PANEL_ENABLED=true` y reinicia bot y panel |
| `UnicodeEncodeError` en consola Windows | Consola sin UTF-8 | Usa `chcp 65001` o PowerShell; el bot ya fuerza UTF-8 con sustitución |

Los errores técnicos nunca llegan al usuario final: se guardan en la tabla `error_logs`
y en `data/bot.log`, y el usuario ve un mensaje amable en español.

---

## 16. Comprobaciones del proyecto

```bash
python -m compileall .      # debe terminar sin errores
python main.py              # arranca sin configuración previa (crea la BD solo)
```

Puntos garantizados:

- Esquema SQLite auto-creado con las 28 tablas requeridas e índices.
- Slash commands y comandos de prefijo en los 45 comandos de alto nivel.
- Tickets con **modal de razón obligatorio** y **cierre con borrado automático**.
- Sorteos, encuestas, recordatorios y tempbans **sobreviven a reinicios** (todo se
  recalcula desde la base de datos).
- Sin `GEMINI_API_KEY` el bot **no crashea**.
- **Cero secretos** en el paquete: `.env` está excluido y `.env.example` no contiene claves.

---

Hecho con ❤️ para comunidades gaming. — **KorianMC Bot v1.0.0**
