// =====================================================================
// 01_colecciones.js  ·  MongoDB
//  - evidencias_entrega : FUENTE OFICIAL de las pruebas de entrega
//  - proyeccion_envios  : VISTA DERIVADA para búsqueda (se reconstruye)
// Se ejecuta solo la primera vez que se crea el volumen mongo_datos.
// =====================================================================
db = db.getSiblingDB("mensajeria");

// ---------- Evidencias de entrega (datos heterogéneos) ---------------
// $jsonSchema exige solo lo indispensable; el resto (extras) es libre.
db.createCollection("evidencias_entrega", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: [
        "_id",
        "envio_codigo",
        "evento_id",
        "receptor",
        "foto",
        "estado",
        "registrada_en",
      ],
      properties: {
        _id: { bsonType: "string", description: "EV-{codigo del envío}" },
        envio_codigo: { bsonType: "string" },
        evento_id: {
          bsonType: "string",
          description: "evento ENTREGA en PostgreSQL",
        },
        receptor: {
          bsonType: "object",
          required: ["nombre"],
          properties: { nombre: { bsonType: "string", minLength: 2 } },
        },
        foto: {
          bsonType: "object",
          required: ["tipo", "valor"],
          properties: {
            tipo: { enum: ["url", "referencia_archivo"] },
            valor: { bsonType: "string", minLength: 3 },
          },
        },
        estado: { enum: ["PENDIENTE", "CONFIRMADA"] },
        registrada_en: { bsonType: "date" },
      },
    },
  },
  validationAction: "error",
});
// Una sola evidencia por envío
db.evidencias_entrega.createIndex(
  { envio_codigo: 1 },
  { unique: true, name: "uq_evidencia_envio" },
);

// ---------- Proyección para la consulta operativa --------------------
db.createCollection("proyeccion_envios", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["_id", "codigo", "estado", "version"],
      properties: {
        _id: { bsonType: "string", description: "mismo código del envío" },
        codigo: { bsonType: "string" },
        estado: { bsonType: "string" },
        version: { bsonType: "int" },
      },
    },
  },
});
// Filtros combinados del panel de despacho: estado + zona + fecha
db.proyeccion_envios.createIndex(
  { estado: 1, "zona_destino.codigo": 1, creado_en: -1 },
  { name: "ix_estado_zona_fecha" },
);
db.proyeccion_envios.createIndex({ creado_en: -1 }, { name: "ix_fecha" });
// Búsqueda por palabras del destinatario (minúsculas y sin tildes), índice multiclave
db.proyeccion_envios.createIndex({ palabras: 1 }, { name: "ix_palabras" });

print("MongoDB: colecciones e índices creados");
