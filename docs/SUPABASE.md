# Configurar Supabase Auth

Esta parte requiere la cuenta del equipo. El código está integrado, pero no se ha
creado un proyecto externo ni se han probado credenciales reales en este entorno.

1. Crear o elegir un proyecto en [Supabase](https://supabase.com/dashboard).
2. En la configuración del proyecto obtener la URL y una **publishable key**
   (la clave `anon` heredada también es válida según la configuración del proyecto).
   No usar `service_role` ni secret key en el servidor de monitoreo.
3. En Authentication / Users crear dos usuarios de laboratorio confirmados: uno
   LECTOR y uno ADMIN. Usar contraseñas de 1 a 64 caracteres ASCII imprimibles sin
   `|`, CR o LF, respetando además la política de contraseñas del proveedor.
4. Asignar `app_metadata.perfil` usando una herramienta administrativa del proveedor.
   No usar `user_metadata`: ese objeto lo puede modificar el usuario.

Una forma precisa de asignarlo es ejecutar lo siguiente en el SQL Editor del proyecto,
reemplazando los correos por las cuentas **ya creadas**:

```sql
update auth.users
set raw_app_meta_data = coalesce(raw_app_meta_data, '{}'::jsonb)
    || '{"perfil":"LECTOR"}'::jsonb
where email = 'lector-del-equipo@example.com';

update auth.users
set raw_app_meta_data = coalesce(raw_app_meta_data, '{}'::jsonb)
    || '{"perfil":"ADMIN"}'::jsonb
where email = 'admin-del-equipo@example.com';

select email, raw_app_meta_data ->> 'perfil' as perfil
from auth.users
where email in ('lector-del-equipo@example.com', 'admin-del-equipo@example.com');
```

El SELECT debe mostrar exactamente las dos cuentas y sus perfiles. La operación
agrega el campo sin borrar los metadatos existentes. También puede usarse la API
administrativa de Supabase desde una herramienta segura externa al proyecto.

En la terminal donde se ejecutará el servidor:

```sh
export SUPABASE_URL='https://TU-PROYECTO.supabase.co'
export SUPABASE_PUBLISHABLE_KEY='TU-CLAVE-PUBLICABLE'
./build/server 5000 monitor.log
```

La URL no debe terminar en `/`. El programa toma variables del entorno; no carga
automáticamente un archivo `.env`. `.env` está ignorado por Git si el equipo decide
crear uno y cargarlo desde su propia terminal.

Después, iniciar `clients/client.py` y comprobar:

| Caso | Resultado |
|---|---|
| Cuenta confirmada con LECTOR | ACK AUTH LECTOR; CURRENT/HISTORY permitidos |
| Cuenta confirmada con ADMIN | ACK AUTH ADMIN; también EVENTS |
| Clave incorrecta o cuenta rechazada | AUTH_FAILED |
| Cuenta válida sin perfil reconocido | FORBIDDEN |
| Sin configuración, fallo DNS/TLS, límite o timeout del proveedor | AUTH_UNAVAILABLE |

La consulta de autenticación es POST HTTPS a `/auth/v1/token?grant_type=password`,
con cabecera `apikey` y JSON `email/password`. Se verifica certificado y nombre,
se limita a 3 s, no se siguen redirecciones y no se registran tokens ni cuerpos HTTP.
Las respuestas inesperadas nunca conceden acceso.

Referencias oficiales: [autenticación con contraseña](https://supabase.com/docs/guides/auth/passwords),
[API de Supabase Auth](https://github.com/supabase/auth),
[metadatos de autorización](https://supabase.com/docs/guides/database/postgres/row-level-security).
