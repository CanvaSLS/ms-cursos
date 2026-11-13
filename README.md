# ms-cursos

Cursos, módulos, matrículas y materiales (S3 con URLs prefirmadas bajo `tenant_id=<T>/`).

- **Base de datos:** Amazon **RDS MySQL**, un **esquema por tenant** (`cursos_utec`, `cursos_norte`, `cursos_andino`) con tablas `cursos`, `modulos`, `matriculas` (claves foráneas, `ON DELETE CASCADE`). Se crean solas la primera vez que el tenant usa el servicio.
- **Publica:** ModuloPublicado
- **Consume:** —
- **Seguridad:** Lambda Authorizer de `ms-auth` en todas las rutas; el tenant sale del token, nunca del cliente.

## Endpoints
| Método | Ruta | Acceso | Handler |
|---|---|---|---|
| GET | `/cursos` | token | `handler.listar_cursos` |
| POST | `/cursos` | token | `handler.crear_curso` |
| PUT | `/cursos/{curso_id}` | token | `handler.modificar_curso` |
| DELETE | `/cursos/{curso_id}` | token | `handler.eliminar_curso` |
| GET | `/cursos/{curso_id}/modulos` | token | `handler.listar_modulos` |
| POST | `/cursos/{curso_id}/modulos` | token | `handler.publicar_modulo` |
| POST | `/cursos/{curso_id}/matriculas` | token | `handler.matricular_estudiante` |
| GET | `/cursos/{curso_id}/matriculas` | token | `handler.listar_matriculas` |
| POST | `/cursos/{curso_id}/modulos/{modulo_id}/materiales` | token | `handler.agregar_material` |
| GET | `/cursos/{curso_id}/modulos/{modulo_id}/materiales/{material_id}/descarga` | token | `handler.descargar_material` |

## Desplegar
```bash
# con credenciales del Learner Lab vigentes y tras desplegar infra-base (RDS) y ms-auth
export DB_PASSWORD='la-misma-de-infra-base'
pip install -r requirements.txt -t vendor          # driver pymysql (Python puro) -> se empaqueta en ./vendor
npx --yes serverless@3 deploy --stage dev
```
Requiere los stacks sp-infra-dev y ms-auth-dev ya desplegados
