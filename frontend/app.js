/* Mensajería urbana · interfaz web (JavaScript sin frameworks).
   El navegador SOLO habla con /api: nunca con las bases de datos. */
'use strict';

// ------------------------------------------------------------------ utilidades
const $ = (sel, raiz = document) => raiz.querySelector(sel);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fecha = (v) => (v ? new Date(v).toLocaleString('es-EC') : '—');
const uuid = () => (crypto.randomUUID ? crypto.randomUUID()
  : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => { const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 3 | 8)).toString(16); }));
const ETIQUETA = { CREADO: 'Creado', ASIGNADO: 'Asignado', RETIRADO: 'Retirado', EN_TRANSITO: 'En tránsito', INCIDENCIA: 'Incidencia', ENTREGADO: 'Entregado', CANCELADO: 'Cancelado' };
const TIPO_EVENTO = { CREACION: 'Envío registrado', ASIGNACION: 'Repartidor asignado', REASIGNACION: 'Repartidor reasignado', ACEPTACION: 'Asignación aceptada', LIBERACION: 'Repartidor liberado', CAMBIO_ESTADO: 'Cambio de estado', ENTREGA: 'Entrega confirmada', DATOS_ACTUALIZADOS: 'Datos de destino corregidos' };
const estado = (e) => `<span class="estado e-${esc(e)}">${esc(ETIQUETA[e] || e)}</span>`;

// Sesión: el token vive solo en sessionStorage (sin claves de bases de datos en el frontend)
const sesion = {
  get() { try { return JSON.parse(sessionStorage.getItem('mu_sesion')); } catch { return null; } },
  set(v) { try { sessionStorage.setItem('mu_sesion', JSON.stringify(v)); } catch { /* sin almacenamiento */ } },
  borrar() { try { sessionStorage.removeItem('mu_sesion'); } catch { /* nada */ } },
};
const usuario = () => sesion.get()?.usuario || null;
const rol = () => usuario()?.rol || null;

let temporizadorAviso = null;
function aviso(texto, tipo = 'ok') {
  clearTimeout(temporizadorAviso);                       // un aviso nuevo no lo borra el temporizador anterior
  $('#aviso').innerHTML = `<div class="msg ${tipo}">${esc(texto)}</div>`;
  if (tipo !== 'error') temporizadorAviso = setTimeout(() => { $('#aviso').innerHTML = ''; }, 6000);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
const mensajeError = (e) => (e.detalleCampos ? `${e.mensaje}: ${e.detalleCampos}` : e.mensaje);

async function api(ruta, { metodo = 'GET', cuerpo, cabeceras = {} } = {}) {
  const h = { ...cabeceras };
  if (cuerpo !== undefined) h['Content-Type'] = 'application/json';
  const s = sesion.get();
  if (s?.token) h.Authorization = `Bearer ${s.token}`;
  let r;
  try {
    r = await fetch(`/api${ruta}`, { method: metodo, headers: h, body: cuerpo !== undefined ? JSON.stringify(cuerpo) : undefined });
  } catch {
    throw { status: 0, codigo: 'SIN_CONEXION', mensaje: 'No hay conexión con la API. Puede reintentar.' };
  }
  const datos = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = datos.error || {};
    if (r.status === 401 && ruta !== '/auth/login') { sesion.borrar(); pintarMenu(); }
    const campos = (err.detalle?.campos || []).map((c) => `${c.campo}: ${c.problema}`).join('; ');
    throw { status: r.status, codigo: err.codigo || 'ERROR', mensaje: err.mensaje || `Error ${r.status}`, detalle: err.detalle, detalleCampos: campos };
  }
  return datos;
}

/* Idempotencia en la web: cada acción conserva su evento_id hasta recibir éxito.
   Si la red falla y el usuario vuelve a pulsar, se reenvía el MISMO id y la API no duplica. */
const pendientes = {};
async function accion(clave, ejecutar, boton) {
  pendientes[clave] = pendientes[clave] || uuid();
  if (boton) boton.disabled = true;
  try {
    const r = await ejecutar(pendientes[clave]);
    delete pendientes[clave];
    return r;
  } catch (e) {
    if (e.status && e.status !== 0 && e.status !== 503) delete pendientes[clave];   // error de negocio: no reintentar con el mismo id
    throw e;
  } finally { if (boton) boton.disabled = false; }
}
const datosForm = (form) => Object.fromEntries(new FormData(form).entries());
const sinVacios = (o) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== '' && v !== null && v !== undefined));
let catalogo = null;
async function catalogos() { if (!catalogo) catalogo = await api('/catalogos'); return catalogo; }
const opciones = (lista, valor = 'codigo', texto = 'nombre', vacio) =>
  (vacio !== undefined ? `<option value="">${esc(vacio)}</option>` : '') + lista.map((x) => `<option value="${esc(x[valor])}">${esc(x[texto])}</option>`).join('');

// ------------------------------------------------------------------ navegación por rol
const MENU = [
  { ruta: 'seguimiento', texto: 'Seguimiento', roles: null },
  { ruta: 'nuevo', texto: 'Nuevo envío', roles: ['CLIENTE', 'OPERADOR', 'ADMIN'] },
  { ruta: 'mis-envios', texto: 'Mis envíos', roles: ['CLIENTE'] },
  { ruta: 'despacho', texto: 'Despacho', roles: ['OPERADOR', 'ADMIN'] },
  { ruta: 'repartidor', texto: 'Mis tareas', roles: ['REPARTIDOR'] },
  { ruta: 'admin', texto: 'Administración', roles: ['ADMIN'] },
  { ruta: 'tecnico', texto: 'Técnico', roles: ['OPERADOR', 'ADMIN'] },
];

function pintarMenu(actual) {
  const r = rol();
  $('#menu').innerHTML = MENU.filter((m) => !m.roles || m.roles.includes(r))
    .map((m) => `<a href="#/${m.ruta}" class="${m.ruta === actual ? 'activo' : ''}">${m.texto}</a>`).join('');
  const u = usuario();
  $('#sesion').innerHTML = u
    ? `<span>${esc(u.nombre)} · <b>${esc(u.rol)}</b></span><button id="salir">Salir</button>`
    : '<a href="#/inicio">Iniciar sesión</a>';
  if (u) $('#salir').onclick = () => { sesion.borrar(); location.hash = '#/inicio'; };
}

function leerRuta() {
  const [ruta, query] = (location.hash.replace(/^#\//, '') || 'inicio').split('?');
  return { ruta, params: new URLSearchParams(query || '') };
}

async function enrutar() {
  const { ruta, params } = leerRuta();
  const vista = VISTAS[ruta] || VISTAS.inicio;
  const permitido = !vista.roles || vista.roles.includes(rol());
  pintarMenu(ruta);
  const cont = $('#vista');
  if (!permitido) {
    cont.innerHTML = '<div class="tarjeta"><h2>Acceso restringido</h2><p>Inicie sesión con un usuario autorizado para esta pantalla.</p><a href="#/inicio">Ir a iniciar sesión</a></div>';
    return;
  }
  cont.innerHTML = '<p class="pequeno">Cargando…</p>';
  try { await vista.pintar(cont, params); } catch (e) { cont.innerHTML = `<div class="msg error">${esc(mensajeError(e))}</div>`; }
}

// ------------------------------------------------------------------ vistas
const VISTAS = {};

// 0. Inicio: login + seguimiento público
VISTAS.inicio = {
  async pintar(c) {
    const u = usuario();
    c.innerHTML = `
      <h1>Mensajería urbana</h1>
      <p class="sub">Registro de envíos, despacho, seguimiento y evidencias de entrega con persistencia políglota.</p>
      <div class="rejilla">
        <div class="tarjeta">
          <h2>${u ? 'Sesión iniciada' : 'Iniciar sesión'}</h2>
          ${u ? `<p>Conectado como <b>${esc(u.nombre)}</b> (${esc(u.rol)}). Use el menú superior.</p>` : `
          <form id="f-login" class="campos">
            <label>Usuario <input name="usuario" required autocomplete="username" placeholder="operador1"></label>
            <label>Clave <input name="clave" type="password" required autocomplete="current-password"></label>
            <div class="acciones"><button>Entrar</button></div>
          </form>
          <p class="pequeno">Usuarios de demostración (ficticios): cliente1 · operador1 · repartidor1 … repartidor4 · admin. Las claves están en el README.</p>`}
        </div>
        <div class="tarjeta">
          <h2>Seguimiento de un envío</h2>
          <form id="f-buscar" class="acciones" style="margin-top:0">
            <input name="codigo" required placeholder="MU-AAMMDD-000001" style="flex:1">
            <button>Consultar</button>
          </form>
          <p class="pequeno">No necesita iniciar sesión.</p>
        </div>
      </div>`;
    $('#f-buscar').onsubmit = (ev) => { ev.preventDefault(); location.hash = `#/seguimiento?codigo=${encodeURIComponent(datosForm(ev.target).codigo.trim())}`; };
    const f = $('#f-login');
    if (f) f.onsubmit = async (ev) => {
      ev.preventDefault();
      const b = $('button', f); b.disabled = true;
      try {
        const r = await api('/auth/login', { metodo: 'POST', cuerpo: datosForm(f) });
        sesion.set(r);
        aviso(`Bienvenido, ${r.usuario.nombre}`);
        location.hash = { CLIENTE: '#/nuevo', OPERADOR: '#/despacho', ADMIN: '#/despacho', REPARTIDOR: '#/repartidor' }[r.usuario.rol];
      } catch (e) { aviso(mensajeError(e), 'error'); } finally { b.disabled = false; }
    };
  },
};

// 1. Formulario de solicitud y comprobante
VISTAS.nuevo = {
  roles: ['CLIENTE', 'OPERADOR', 'ADMIN'],
  async pintar(c) {
    const cat = await catalogos();
    let clientes = [];
    if (rol() !== 'CLIENTE') clientes = await api('/admin/clientes');
    c.innerHTML = `
      <h1>Nuevo envío</h1><p class="sub">La API valida los datos y genera el código único de seguimiento.</p>
      <div id="comprobante"></div>
      <form id="f-envio" class="tarjeta">
        <div class="campos">
          ${rol() !== 'CLIENTE' ? `<label>Cliente <select name="cliente_id">${opciones(clientes, 'id', 'nombre', '— Sin cliente —')}</select></label>` : ''}
          <label>Tipo de servicio <select name="tipo_servicio" required>${opciones(cat.tipos_servicio)}</select></label>
          <label>Peso (kg) <input name="peso_kg" type="number" step="0.1" min="0.1" max="50" value="1.5" required></label>
          <label>Remitente <input name="remitente_nombre" required minlength="3" value="${esc(usuario().nombre)}"></label>
          <label>Zona de origen <select name="zona_origen" required>${opciones(cat.zonas)}</select></label>
          <label>Dirección de origen <input name="origen_direccion" required minlength="5" placeholder="Av. Ficticia N10-100"></label>
          <label>Destinatario <input name="destinatario_nombre" required minlength="3" placeholder="Nombre ficticio"></label>
          <label>Teléfono destinatario <input name="destinatario_telefono" pattern="0\\d{9}" placeholder="0991234567"></label>
          <label>Zona de destino <select name="zona_destino" required>${opciones(cat.zonas)}</select></label>
          <label>Dirección de destino <input name="destino_direccion" required minlength="5" placeholder="Calle Ejemplo S5-12"></label>
          <label>Lat. origen <input name="origen_lat" type="number" step="0.000001" value="-0.150000" required></label>
          <label>Lng. origen <input name="origen_lng" type="number" step="0.000001" value="-78.480000" required></label>
          <label>Lat. destino <input name="destino_lat" type="number" step="0.000001" value="-0.290000" required></label>
          <label>Lng. destino <input name="destino_lng" type="number" step="0.000001" value="-78.540000" required></label>
        </div>
        <div class="acciones"><button>Registrar envío</button><span class="pequeno">Coordenadas ficticias dentro de Quito.</span></div>
      </form>`;
    const f = $('#f-envio');
    f.onsubmit = async (ev) => {
      ev.preventDefault();
      const d = sinVacios(datosForm(f));
      ['peso_kg', 'origen_lat', 'origen_lng', 'destino_lat', 'destino_lng'].forEach((k) => { d[k] = Number(d[k]); });
      if (d.cliente_id) d.cliente_id = Number(d.cliente_id);
      try {
        // La misma Idempotency-Key se reutiliza si el usuario reintenta tras un fallo de red
        const r = await accion('crear-envio', (clave) => api('/envios', { metodo: 'POST', cuerpo: d, cabeceras: { 'Idempotency-Key': clave } }), $('button', f));
        const e = r.envio;
        $('#comprobante').innerHTML = `
          <div class="tarjeta comprobante">
            <h2>✔ ${r.repetido ? 'Envío ya registrado (reintento sin duplicar)' : 'Envío registrado'}</h2>
            <div class="codigo">${esc(e.codigo)}</div>
            <dl class="kv" style="margin-top:10px">
              <dt>Estado</dt><dd>${estado(e.estado)}</dd><dt>Servicio</dt><dd>${esc(e.tipo_servicio)} · ${e.peso_kg} kg</dd>
              <dt>Ruta</dt><dd>${esc(e.zona_origen)} → ${esc(e.zona_destino)}</dd><dt>Destinatario</dt><dd>${esc(e.destinatario_nombre)}</dd>
              <dt>Registrado</dt><dd>${fecha(e.creado_en)}</dd>
            </dl>
            <div class="acciones"><a href="#/seguimiento?codigo=${esc(e.codigo)}">Ver seguimiento</a></div>
          </div>`;
        aviso(r.mensaje || 'Envío registrado');
        f.reset();
      } catch (e) { aviso(mensajeError(e), 'error'); }
    };
  },
};

// Mis envíos (cliente)
VISTAS['mis-envios'] = {
  roles: ['CLIENTE'],
  async pintar(c) {
    const lista = await api('/envios/mios');
    c.innerHTML = `<h1>Mis envíos</h1><p class="sub">${lista.length} envío(s) · <span class="fuente">postgresql</span></p>
      <div class="tarjeta tabla-scroll"><table><tr><th>Código</th><th>Estado</th><th>Destino</th><th>Destinatario</th><th>Creado</th><th></th></tr>
      ${lista.map((e) => `<tr><td><b>${esc(e.codigo)}</b></td><td>${estado(e.estado)}</td><td>${esc(e.zona_destino)}</td>
        <td>${esc(e.destinatario_nombre)}</td><td>${fecha(e.creado_en)}</td>
        <td><a href="#/detalle?codigo=${esc(e.codigo)}">Detalle</a>
        ${e.estado === 'CREADO' ? ` · <a href="#" data-cancelar="${esc(e.codigo)}">Cancelar</a>` : ''}</td></tr>`).join('')}</table></div>`;
    c.querySelectorAll('[data-cancelar]').forEach((a) => {
      a.onclick = async (ev) => {
        ev.preventDefault();
        const cod = a.dataset.cancelar;
        try {
          await accion(`cancelar-${cod}`, (id) => api(`/envios/${cod}/estado`, { metodo: 'PATCH', cuerpo: { estado_nuevo: 'CANCELADO', evento_id: id } }));
          aviso(`Envío ${cod} cancelado`); enrutar();
        } catch (e) { aviso(mensajeError(e), 'error'); }
      };
    });
  },
};

// 2. Seguimiento por código con línea de tiempo (público)
VISTAS.seguimiento = {
  async pintar(c, p) {
    const codigo = (p.get('codigo') || '').trim();
    c.innerHTML = `<h1>Seguimiento</h1>
      <form id="f-seg" class="tarjeta acciones" style="margin-top:0"><input name="codigo" value="${esc(codigo)}" required placeholder="MU-AAMMDD-000001" style="flex:1"><button>Consultar</button></form>
      <div id="res"></div>`;
    $('#f-seg').onsubmit = (ev) => { ev.preventDefault(); location.hash = `#/seguimiento?codigo=${encodeURIComponent(datosForm(ev.target).codigo.trim())}`; };
    if (!codigo) return;
    const s = await api(`/seguimiento/${encodeURIComponent(codigo)}`);
    $('#res').innerHTML = `
      <div class="rejilla">
        <div class="tarjeta">
          <div class="codigo">${esc(s.codigo)}</div>
          <p>${estado(s.estado)}</p>
          <dl class="kv"><dt>Servicio</dt><dd>${esc(s.tipo_servicio)}</dd><dt>Ruta</dt><dd>${esc(s.zona_origen)} → ${esc(s.zona_destino)}</dd>
            <dt>Para</dt><dd>${esc(s.destinatario)}</dd></dl>
          ${s.prueba_de_entrega ? `<h2 style="margin-top:14px">Prueba de entrega</h2>
            <dl class="kv"><dt>Recibido por</dt><dd>${esc(s.prueba_de_entrega.recibido_por)}</dd>
            <dt>Registrada</dt><dd>${fecha(s.prueba_de_entrega.registrada_en)}</dd><dt>Evidencia</dt><dd>${esc(s.prueba_de_entrega.estado)}</dd></dl>` : ''}
          ${(s.avisos || []).map((a) => `<div class="msg info">${esc(a)}</div>`).join('')}
          <p class="pequeno">Fuente: <span class="fuente">${esc(s.fuente)}</span></p>
        </div>
        <div class="tarjeta"><h2>Línea de tiempo</h2>${lineaTiempo(s.linea_de_tiempo.map((h) => ({ ...h, estado_nuevo: h.estado, actor_rol: h.rol, ocurrido_en: h.fecha })))}</div>
      </div>`;
  },
};

function lineaTiempo(eventos) {
  return `<ul class="linea">${eventos.map((h) => `<li>
    <div><b>${esc(TIPO_EVENTO[h.tipo] || h.tipo)}</b> ${estado(h.estado_nuevo)}</div>
    <div class="cuando">${fecha(h.ocurrido_en)} · ${esc(h.actor || h.actor_rol)}${h.secuencia ? ` · evento #${h.secuencia}` : ''}</div>
    ${h.observacion || h.detalle?.observacion ? `<div>“${esc(h.observacion || h.detalle.observacion)}”</div>` : ''}
    ${h.detalle?.repartidor ? `<div class="pequeno">Repartidor: ${esc(h.detalle.repartidor)}</div>` : ''}
    ${h.detalle?.receptor ? `<div class="pequeno">Recibió: ${esc(h.detalle.receptor)}</div>` : ''}
    ${h.lat ? `<div class="pequeno">Ubicación simulada: ${h.lat}, ${h.lng}</div>` : ''}
  </li>`).join('')}</ul>`;
}

// 3. Panel de despacho: lista, filtros y asignación
VISTAS.despacho = {
  roles: ['OPERADOR', 'ADMIN'],
  async pintar(c, p) {
    const cat = await catalogos();
    const filtro = Object.fromEntries(p.entries());
    if (!p.toString()) filtro.estado = 'CREADO';
    c.innerHTML = `
      <h1>Panel de despacho</h1><p class="sub">Asigne envíos pendientes. La capacidad se verifica en PostgreSQL al confirmar.</p>
      <form id="f-filtro" class="tarjeta">
        <div class="campos">
          <label>Código <input name="codigo" value="${esc(filtro.codigo)}" placeholder="MU-"></label>
          <label>Estado <select name="estado"><option value="">Todos</option>${Object.keys(ETIQUETA).map((e) => `<option ${filtro.estado === e ? 'selected' : ''} value="${e}">${ETIQUETA[e]}</option>`).join('')}</select></label>
          <label>Zona destino <select name="zona">${opciones(cat.zonas, 'codigo', 'nombre', 'Todas')}</select></label>
          <label>Destinatario <input name="destinatario" value="${esc(filtro.destinatario)}"></label>
          <label>Desde <input name="desde" type="date" value="${esc(filtro.desde)}"></label>
          <label>Hasta <input name="hasta" type="date" value="${esc(filtro.hasta)}"></label>
          <label>Fuente <select name="fuente"><option value="auto">MongoDB (proyección)</option><option value="postgresql" ${filtro.fuente === 'postgresql' ? 'selected' : ''}>PostgreSQL (oficial)</option></select></label>
        </div>
        <div class="acciones"><button>Buscar</button><button type="button" class="sec" id="limpiar">Limpiar</button><span id="fuente-res"></span></div>
      </form>
      <div class="tarjeta"><h2>Repartidores</h2><div id="reps" class="tabla-scroll"></div></div>
      <div class="tarjeta"><h2>Envíos</h2><div id="lista" class="tabla-scroll"></div></div>`;
    $('[name=zona]', c).value = filtro.zona || '';
    $('#f-filtro').onsubmit = (ev) => { ev.preventDefault(); location.hash = `#/despacho?${new URLSearchParams(sinVacios(datosForm(ev.target)))}`; };
    $('#limpiar').onclick = () => { location.hash = '#/despacho?estado='; };

    const [reps, temporal] = await Promise.all([api('/despacho/repartidores'), api('/repartidores/estado-temporal').catch(() => [])]);
    const pos = Object.fromEntries(temporal.map((t) => [t.id, t.posicion]));
    $('#reps').innerHTML = `<table><tr><th>Repartidor</th><th>Zona</th><th>Vehículo</th><th>Carga</th><th>Posición (Redis)</th></tr>
      ${reps.filter((r) => r.activo).map((r) => `<tr><td>${esc(r.nombre)}</td><td>${esc(r.zona)}</td><td>${esc(r.vehiculo)}</td>
        <td><span class="barra-cap"><span class="${r.cupos_libres <= 0 ? 'lleno' : ''}" style="width:${Math.min(100, 100 * r.carga_actual / r.capacidad_max)}%"></span></span> ${r.carga_actual}/${r.capacidad_max}</td>
        <td>${pos[r.id]?.vigente ? `<span class="ok-t">vigente</span> ${pos[r.id].lat}, ${pos[r.id].lng} <span class="pequeno">(expira en ${pos[r.id].expira_en_seg}s)</span>` : `<span class="pequeno">${esc(pos[r.id]?.mensaje || 'sin dato')}</span>`}</td></tr>`).join('')}</table>`;

    const q = new URLSearchParams(sinVacios(filtro));
    const res = await api(`/envios?${q}`);
    $('#fuente-res').innerHTML = `Fuente: <span class="fuente">${esc(res.fuente)}</span>`;
    const selRep = (e) => `<select data-rep="${esc(e.codigo)}">${reps.filter((r) => r.activo).map((r) => `<option value="${r.id}" ${r.cupos_libres <= 0 ? 'class="mal-t"' : ''}>${esc(r.nombre)} (${r.carga_actual}/${r.capacidad_max})</option>`).join('')}</select>`;
    $('#lista').innerHTML = res.resultados.length ? `<table><tr><th>Código</th><th>Estado</th><th>Ruta</th><th>Destinatario</th><th>Repartidor</th><th>Creado</th><th>Acción</th></tr>
      ${res.resultados.map((e) => {
        const zonaD = e.zona_destino?.nombre || e.zona_destino; const zonaO = e.zona_origen?.nombre || e.zona_origen;
        const rep = e.repartidor?.nombre || e.repartidor || '—';
        let acc = '';
        if (e.estado === 'CREADO') acc = `${selRep(e)} <button data-asignar="${esc(e.codigo)}">Asignar</button>`;
        else if (['ASIGNADO', 'INCIDENCIA'].includes(e.estado)) acc = `${selRep(e)} <button class="sec" data-reasignar="${esc(e.codigo)}">Reasignar</button> <button class="sec" data-liberar="${esc(e.codigo)}">Liberar</button>`;
        return `<tr><td><a href="#/detalle?codigo=${esc(e.codigo)}"><b>${esc(e.codigo)}</b></a></td><td>${estado(e.estado)}</td><td>${esc(zonaO)} → ${esc(zonaD)}</td>
          <td>${esc(e.destinatario_nombre)}</td><td>${esc(rep)}</td><td>${fecha(e.creado_en)}</td><td style="white-space:nowrap">${acc}</td></tr>`;
      }).join('')}</table>` : '<p class="pequeno">No hay envíos con esos filtros.</p>';

    const repDe = (cod) => Number($(`[data-rep="${cod}"]`).value);
    c.querySelectorAll('[data-asignar]').forEach((b) => { b.onclick = async () => {
      const cod = b.dataset.asignar;
      try {
        const r = await accion(`asignar-${cod}`, (id) => api('/asignaciones', { metodo: 'POST', cuerpo: { codigo: cod, repartidor_id: repDe(cod), evento_id: id } }), b);
        aviso(`${cod} asignado a ${r.capacidad?.repartidor || 'repartidor'} (${r.capacidad?.carga_actual}/${r.capacidad?.capacidad_max}). La lista se actualiza al sincronizar.`); recargarTrasSync();
      } catch (e) { aviso(mensajeError(e), 'error'); }
    }; });
    c.querySelectorAll('[data-reasignar]').forEach((b) => { b.onclick = async () => {
      const cod = b.dataset.reasignar;
      try { await accion(`reasignar-${cod}`, (id) => api(`/asignaciones/${cod}/reasignar`, { metodo: 'POST', cuerpo: { repartidor_id: repDe(cod), evento_id: id } }), b); aviso(`${cod} reasignado`); recargarTrasSync(); }
      catch (e) { aviso(mensajeError(e), 'error'); }
    }; });
    c.querySelectorAll('[data-liberar]').forEach((b) => { b.onclick = async () => {
      const cod = b.dataset.liberar;
      try { await accion(`liberar-${cod}`, (id) => api(`/asignaciones/${cod}/liberar`, { metodo: 'POST', cuerpo: { evento_id: id } }), b); aviso(`${cod} vuelve a pendientes`); recargarTrasSync(); }
      catch (e) { aviso(mensajeError(e), 'error'); }
    }; });
  },
};

// La lista del despacho sale de la proyección (consistencia eventual): se recarga tras ~2,5 s
const recargarTrasSync = () => setTimeout(enrutar, 2500);

// 4. Vista del repartidor: tareas, botones de estado, posición y entrega
VISTAS.repartidor = {
  roles: ['REPARTIDOR'],
  async pintar(c) {
    const t = await api('/repartidor/tareas');
    const pos = t.posicion;
    c.innerHTML = `
      <h1>Mis tareas</h1><p class="sub">${t.tareas.length} envío(s) activo(s) · <span class="fuente">postgresql</span></p>
      <div class="tarjeta"><h2>Mi posición simulada <span class="fuente">redis · TTL</span></h2>
        <p>${pos.vigente ? `<span class="ok-t">Vigente</span>: ${pos.lat}, ${pos.lng} · expira en ${pos.expira_en_seg} s` : `<span class="pequeno">${esc(pos.mensaje)}</span>`}</p>
        <form id="f-pos" class="acciones" style="margin-top:0">
          <input name="lat" type="number" step="0.0001" value="${pos.lat ?? -0.2}" style="width:130px"><input name="lng" type="number" step="0.0001" value="${pos.lng ?? -78.5}" style="width:130px">
          <button>Reportar posición</button><button type="button" class="sec" id="azar">Posición al azar</button><button type="button" class="sec" id="desc">Desconectar</button>
        </form></div>
      <div id="tareas"></div>`;
    const reportar = async (lat, lng) => {
      try { const r = await api('/repartidor/ubicacion', { metodo: 'POST', cuerpo: { lat, lng } }); aviso(r.mensaje); enrutar(); } catch (e) { aviso(mensajeError(e), 'error'); }
    };
    $('#f-pos').onsubmit = (ev) => { ev.preventDefault(); const d = datosForm(ev.target); reportar(Number(d.lat), Number(d.lng)); };
    $('#azar').onclick = () => reportar(Number((-0.30 + Math.random() * 0.2).toFixed(4)), Number((-78.55 + Math.random() * 0.12).toFixed(4)));
    $('#desc').onclick = async () => { try { await api('/repartidor/desconectar', { metodo: 'POST' }); aviso('Posición marcada como no vigente'); enrutar(); } catch (e) { aviso(mensajeError(e), 'error'); } };

    $('#tareas').innerHTML = t.tareas.length ? t.tareas.map((e) => `
      <div class="tarjeta" data-tarea="${esc(e.codigo)}">
        <div class="acciones" style="margin-top:0;justify-content:space-between">
          <div><a href="#/detalle?codigo=${esc(e.codigo)}" class="codigo" style="font-size:16px">${esc(e.codigo)}</a> ${estado(e.estado)} ${e.aceptada ? '<span class="pequeno">· aceptada</span>' : ''}</div>
          <span class="pequeno">${esc(e.tipo_servicio)} · ${e.peso_kg} kg</span>
        </div>
        <dl class="kv" style="margin-top:8px"><dt>Retiro</dt><dd>${esc(e.origen_direccion)} (${esc(e.zona_origen)})</dd>
          <dt>Entrega</dt><dd>${esc(e.destino_direccion)} (${esc(e.zona_destino)}) · ${esc(e.destinatario_nombre)} ${esc(e.destinatario_telefono || '')}</dd></dl>
        <div class="acciones">
          ${e.acciones.includes('ACEPTAR') && !e.aceptada ? '<button data-acc="ACEPTAR">Aceptar asignación</button>' : ''}
          ${e.acciones.includes('RETIRADO') ? '<button data-acc="RETIRADO">Marcar retirado</button>' : ''}
          ${e.acciones.includes('EN_TRANSITO') ? `<button data-acc="EN_TRANSITO">${e.estado === 'INCIDENCIA' ? 'Reanudar (en tránsito)' : 'En tránsito'}</button>` : ''}
          ${e.acciones.includes('INCIDENCIA') ? '<input data-obs placeholder="Describa la incidencia" style="flex:1;min-width:180px"><button class="sec" data-acc="INCIDENCIA">Reportar incidencia</button>' : ''}
        </div>
        ${e.acciones.includes('ENTREGADO') ? `
          <form class="campos" data-entrega style="margin-top:12px;border-top:1px solid var(--borde);padding-top:12px">
            <label>Recibido por <input name="nombre" required minlength="2" value="${esc(e.destinatario_nombre)}"></label>
            <label>Relación <select name="relacion"><option>Titular</option><option>Familiar</option><option>Portería</option><option>Vecino</option></select></label>
            <label>Foto (referencia) <input name="foto" required minlength="3" value="foto_${esc(e.codigo)}.jpg"></label>
            <label>Observación <input name="observacion" placeholder="Opcional"></label>
            <label>Firma simulada <select name="firma"><option value="si">Sí</option><option value="no">No</option></select></label>
            <div class="acciones"><button>Confirmar entrega</button></div>
          </form>` : ''}
      </div>`).join('') : '<div class="tarjeta"><p>No tiene envíos asignados.</p></div>';

    c.querySelectorAll('[data-tarea]').forEach((card) => {
      const cod = card.dataset.tarea;
      card.querySelectorAll('[data-acc]').forEach((b) => { b.onclick = async () => {
        const acc = b.dataset.acc;
        try {
          if (acc === 'ACEPTAR') {
            await accion(`aceptar-${cod}`, (id) => api(`/repartidor/tareas/${cod}/aceptar`, { metodo: 'POST', cuerpo: { evento_id: id } }), b);
          } else {
            const obs = $('[data-obs]', card)?.value?.trim();
            const cuerpo = { estado_nuevo: acc, observacion: acc === 'INCIDENCIA' ? obs : undefined, lat: pos.lat, lng: pos.lng };
            await accion(`${acc}-${cod}`, (id) => api(`/envios/${cod}/estado`, { metodo: 'PATCH', cuerpo: { ...sinVacios(cuerpo), evento_id: id } }), b);
          }
          aviso(`${cod}: ${acc === 'ACEPTAR' ? 'asignación aceptada' : ETIQUETA[acc]}`); enrutar();
        } catch (e) { aviso(mensajeError(e), 'error'); }
      }; });
      const fe = $('[data-entrega]', card);
      if (fe) fe.onsubmit = async (ev) => {
        ev.preventDefault();
        const d = datosForm(fe);
        const cuerpo = sinVacios({ receptor: { nombre: d.nombre, relacion: d.relacion }, foto: { tipo: 'referencia_archivo', valor: d.foto },
          observacion: d.observacion, extras: { firma_simulada: d.firma === 'si', dispositivo: 'web-repartidor' }, lat: pos.lat, lng: pos.lng });
        try {
          const r = await accion(`entrega-${cod}`, (id) => api(`/envios/${cod}/entrega`, { metodo: 'POST', cuerpo: { ...cuerpo, evento_id: id } }), $('button', fe));
          aviso(`${cod} entregado · evidencia ${r.evidencia?.estado || 'registrada'}`); location.hash = `#/detalle?codigo=${cod}`;
        } catch (e) { aviso(mensajeError(e), 'error'); }
      };
    });
  },
};

// 5. Detalle del envío: estados y evidencia
VISTAS.detalle = {
  roles: ['CLIENTE', 'OPERADOR', 'ADMIN', 'REPARTIDOR'],
  async pintar(c, p) {
    const cod = p.get('codigo');
    const d = await api(`/envios/${encodeURIComponent(cod)}`);
    const e = d.envio; const ev = d.evidencia; const op = ['OPERADOR', 'ADMIN'].includes(rol());
    c.innerHTML = `
      <h1>Detalle del envío</h1>
      <p class="sub"><span class="codigo" style="font-size:18px">${esc(e.codigo)}</span> ${estado(e.estado)} · versión ${e.version}</p>
      ${(d.avisos || []).map((a) => `<div class="msg info">${esc(a)}</div>`).join('')}
      <div class="rejilla">
        <div class="tarjeta"><h2>Datos <span class="fuente">postgresql</span></h2>
          <dl class="kv"><dt>Servicio</dt><dd>${esc(e.tipo_servicio)} · ${e.peso_kg} kg</dd>
            <dt>Remitente</dt><dd>${esc(e.remitente_nombre)}</dd>
            <dt>Origen</dt><dd>${esc(e.origen_direccion)} · ${esc(e.zona_origen)}</dd>
            <dt>Destino</dt><dd>${esc(e.destino_direccion)} · ${esc(e.zona_destino)}</dd>
            <dt>Destinatario</dt><dd>${esc(e.destinatario_nombre)} ${esc(e.destinatario_telefono || '')}</dd>
            <dt>Repartidor</dt><dd>${esc(e.repartidor || '—')}</dd>
            <dt>Posición</dt><dd>${d.posicion_repartidor ? (d.posicion_repartidor.vigente ? `${d.posicion_repartidor.lat}, ${d.posicion_repartidor.lng} <span class="fuente">redis</span>` : esc(d.posicion_repartidor.mensaje)) : '—'}</dd>
            <dt>Creado</dt><dd>${fecha(e.creado_en)}</dd><dt>Actualizado</dt><dd>${fecha(e.actualizado_en)}</dd></dl>
          ${op && !['ENTREGADO', 'CANCELADO'].includes(e.estado) ? `
            <h2 style="margin-top:16px">Corregir destino</h2>
            <form id="f-dest" class="campos">
              <label>Destinatario <input name="destinatario_nombre" required minlength="3" value="${esc(e.destinatario_nombre)}"></label>
              <label>Dirección <input name="destino_direccion" minlength="5" value="${esc(e.destino_direccion)}"></label>
              <label>Zona destino <select name="zona_destino">${opciones((await catalogos()).zonas)}</select></label>
              <div class="acciones"><button>Guardar corrección</button></div>
            </form>
            <div class="acciones"><input id="obs-inc" placeholder="Observación" style="flex:1">
              ${['ASIGNADO', 'RETIRADO', 'EN_TRANSITO'].includes(e.estado) ? '<button class="sec" data-est="INCIDENCIA">Registrar incidencia</button>' : ''}
              ${e.estado === 'INCIDENCIA' ? '<button class="sec" data-est="EN_TRANSITO">Resolver incidencia</button>' : ''}
              ${['CREADO', 'ASIGNADO', 'INCIDENCIA'].includes(e.estado) ? '<button class="peligro" data-est="CANCELADO">Cancelar envío</button>' : ''}
            </div>` : ''}
        </div>
        <div class="tarjeta"><h2>Evidencia de entrega <span class="fuente">mongodb</span></h2>
          ${ev ? `<dl class="kv"><dt>Estado</dt><dd><span class="${ev.estado === 'CONFIRMADA' ? 'ok-t' : 'mal-t'}">${esc(ev.estado)}</span></dd>
            <dt>Recibido por</dt><dd>${esc(ev.receptor?.nombre)} ${ev.receptor?.relacion ? `(${esc(ev.receptor.relacion)})` : ''}</dd>
            <dt>Foto</dt><dd>${esc(ev.foto?.tipo)}: ${esc(ev.foto?.valor)}</dd>
            <dt>Observación</dt><dd>${esc(ev.observacion || '—')}</dd>
            <dt>Registrada</dt><dd>${fecha(ev.registrada_en)}</dd></dl>
            <p class="pequeno">Documento completo (atributos variables):</p><pre>${esc(JSON.stringify(ev, null, 2))}</pre>`
            : '<p class="pequeno">Aún no hay evidencia: se registra al confirmar la entrega.</p>'}
        </div>
      </div>
      <div class="tarjeta"><h2>Historial de estados <span class="fuente">postgresql · historial_estados (inmutable)</span></h2>${lineaTiempo(d.historial)}</div>`;
    const fd = $('#f-dest');
    if (fd) {
      $('[name=zona_destino]', fd).value = e.zona_destino_codigo;
      fd.onsubmit = async (evn) => {
        evn.preventDefault();
        try { await accion(`dest-${cod}`, (id) => api(`/envios/${cod}/destinatario`, { metodo: 'PATCH', cuerpo: { ...sinVacios(datosForm(fd)), evento_id: id } }), $('button', fd));
          aviso('Destino corregido en PostgreSQL; la búsqueda se actualizará en segundos'); enrutar(); } catch (er) { aviso(mensajeError(er), 'error'); }
      };
    }
    c.querySelectorAll('[data-est]').forEach((b) => { b.onclick = async () => {
      const est = b.dataset.est; const obs = $('#obs-inc').value.trim();
      try { await accion(`${est}-${cod}`, (id) => api(`/envios/${cod}/estado`, { metodo: 'PATCH', cuerpo: sinVacios({ estado_nuevo: est, observacion: obs, evento_id: id }) }), b);
        aviso(`${cod}: ${ETIQUETA[est]}`); enrutar(); } catch (er) { aviso(mensajeError(er), 'error'); }
    }; });
  },
};

// 6. Panel administrativo: zonas, tipos, repartidores y capacidades
VISTAS.admin = {
  roles: ['ADMIN'],
  async pintar(c) {
    const [zonas, tipos, reps] = await Promise.all([api('/admin/zonas'), api('/admin/tipos-servicio'), api('/despacho/repartidores')]);
    catalogo = null;
    c.innerHTML = `
      <h1>Administración</h1><p class="sub">Zonas, tipos de servicio, repartidores y capacidad operativa · <span class="fuente">postgresql</span></p>
      <div class="tarjeta"><h2>Repartidores y capacidad</h2><div class="tabla-scroll"><table>
        <tr><th>Repartidor</th><th>Zona</th><th>Vehículo</th><th>Carga</th><th>Capacidad</th><th>Activo</th><th></th></tr>
        ${reps.map((r) => `<tr data-r="${r.id}"><td><input name="nombre" value="${esc(r.nombre)}"></td>
          <td><select name="zona">${opciones(zonas.filter((z) => z.activa))}</select></td>
          <td><select name="vehiculo">${['MOTO', 'BICICLETA', 'AUTO', 'A_PIE'].map((v) => `<option ${v === r.vehiculo ? 'selected' : ''}>${v}</option>`).join('')}</select></td>
          <td>${r.carga_actual}</td><td><input name="capacidad_max" type="number" min="1" max="20" value="${r.capacidad_max}" style="width:70px"></td>
          <td><input name="activo" type="checkbox" ${r.activo ? 'checked' : ''}></td><td><button class="sec" data-guardar-r>Guardar</button></td></tr>`).join('')}
      </table></div>
      <h2 style="margin-top:14px">Nuevo repartidor</h2>
      <form id="f-rep" class="campos" autocomplete="off">
        <label>Nombre <input name="nombre" required minlength="3"></label>
        <label>Zona <select name="zona">${opciones(zonas.filter((z) => z.activa))}</select></label>
        <label>Vehículo <select name="vehiculo"><option>MOTO</option><option>BICICLETA</option><option>AUTO</option><option>A_PIE</option></select></label>
        <label>Capacidad <input name="capacidad_max" type="number" min="1" max="20" value="2" required></label>
        <label>Usuario <input name="usuario" required pattern="[a-z0-9_]+" placeholder="repartidor5" autocomplete="off"></label>
        <label>Clave (mín. 8) <input name="clave" type="password" required minlength="8" autocomplete="new-password"></label>
        <div class="acciones"><button>Crear repartidor</button></div>
      </form></div>
      <div class="rejilla">
        <div class="tarjeta"><h2>Zonas</h2><table><tr><th>Código</th><th>Nombre</th><th>Activa</th><th></th></tr>
          ${zonas.map((z) => `<tr data-z="${z.id}"><td>${esc(z.codigo)}</td><td><input name="nombre" value="${esc(z.nombre)}"></td>
            <td><input name="activa" type="checkbox" ${z.activa ? 'checked' : ''}></td><td><button class="sec" data-guardar-z>Guardar</button></td></tr>`).join('')}</table>
          <form id="f-zona" class="acciones"><input name="codigo" required placeholder="Código (ej. TUM)" style="width:140px"><input name="nombre" required placeholder="Nombre" style="flex:1"><button>Agregar</button></form></div>
        <div class="tarjeta"><h2>Tipos de servicio</h2><table><tr><th>Código</th><th>Nombre</th><th>Horas</th><th>Activo</th><th></th></tr>
          ${tipos.map((t) => `<tr data-t="${t.id}"><td>${esc(t.codigo)}</td><td><input name="nombre" value="${esc(t.nombre)}"></td>
            <td><input name="horas_objetivo" type="number" min="1" value="${t.horas_objetivo}" style="width:70px"></td>
            <td><input name="activo" type="checkbox" ${t.activo ? 'checked' : ''}></td><td><button class="sec" data-guardar-t>Guardar</button></td></tr>`).join('')}</table>
          <form id="f-tipo" class="acciones"><input name="codigo" required placeholder="Código" style="width:120px"><input name="nombre" required placeholder="Nombre" style="flex:1"><input name="horas_objetivo" type="number" min="1" value="12" style="width:70px"><button>Agregar</button></form></div>
      </div>`;
    reps.forEach((r) => { $(`[data-r="${r.id}"] [name=zona]`, c).value = r.zona_codigo; });
    const fila = (tr) => Object.fromEntries([...tr.querySelectorAll('input,select')].map((i) => [i.name, i.type === 'checkbox' ? i.checked : (i.type === 'number' ? Number(i.value) : i.value)]));
    const guardar = (sel, attr, ruta, msg) => c.querySelectorAll(sel).forEach((b) => { b.onclick = async () => {
      const tr = b.closest('tr');
      try { await api(`${ruta}/${tr.dataset[attr]}`, { metodo: 'PATCH', cuerpo: fila(tr) }); aviso(msg); enrutar(); } catch (e) { aviso(mensajeError(e), 'error'); }
    }; });
    guardar('[data-guardar-r]', 'r', '/admin/repartidores', 'Repartidor actualizado');
    guardar('[data-guardar-z]', 'z', '/admin/zonas', 'Zona actualizada');
    guardar('[data-guardar-t]', 't', '/admin/tipos-servicio', 'Tipo de servicio actualizado');
    const crear = (id, ruta, msg, num = []) => { $(id).onsubmit = async (ev) => {
      ev.preventDefault(); const d = datosForm(ev.target); num.forEach((k) => { d[k] = Number(d[k]); });
      try { await api(ruta, { metodo: 'POST', cuerpo: d }); aviso(msg); enrutar(); } catch (e) { aviso(mensajeError(e), 'error'); }
    }; };
    crear('#f-rep', '/admin/repartidores', 'Repartidor creado', ['capacidad_max']);
    crear('#f-zona', '/admin/zonas', 'Zona creada');
    crear('#f-tipo', '/admin/tipos-servicio', 'Tipo de servicio creado', ['horas_objetivo']);
  },
};

// Pantalla técnica: salud, sincronizador y reconciliación (sin credenciales)
VISTAS.tecnico = {
  roles: ['OPERADOR', 'ADMIN'],
  async pintar(c) {
    const [salud, sync, rec, temporal] = await Promise.all([
      fetch('/api/health').then((r) => r.json()), api('/admin/sync/estado'), api('/admin/reconciliacion'), api('/repartidores/estado-temporal').catch(() => [])]);
    const ind = (v) => (v === 'ok' || v === 'activo' || v === 'OK' ? `<span class="ok-t">${esc(v)}</span>` : `<span class="mal-t">${esc(v)}</span>`);
    c.innerHTML = `
      <h1>Estado técnico</h1><p class="sub">Actualizado ${fecha(new Date())} · <a href="#" id="refrescar">Actualizar</a></p>
      <div class="rejilla">
        <div class="tarjeta"><h2>Salud (GET /health)</h2><dl class="kv"><dt>Estado general</dt><dd>${ind(salud.estado)}</dd>
          ${Object.entries(salud.dependencias).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${ind(v)}</dd>`).join('')}</dl></div>
        <div class="tarjeta"><h2>Sincronizador (outbox → MongoDB)</h2><dl class="kv">
          <dt>Proceso</dt><dd>${ind(sync.sincronizador)}</dd><dt>Último latido</dt><dd>${fecha(sync.ultimo_latido)}</dd>
          <dt>Pendientes</dt><dd class="indicador">${sync.outbox.pendientes}</dd><dt>Con error</dt><dd>${sync.outbox.con_error}</dd>
          <dt>Retraso actual</dt><dd>${sync.outbox.retraso_seg ?? 0} s</dd><dt>Retraso promedio (10 min)</dt><dd>${sync.outbox.retraso_promedio_seg ?? '—'} s</dd>
          <dt>Envíos PostgreSQL</dt><dd>${sync.envios_postgresql}</dd><dt>Documentos MongoDB</dt><dd>${sync.documentos_mongodb ?? 'MongoDB no disponible'}</dd>
          <dt>Último error</dt><dd>${esc(sync.ultimo_error || 'ninguno')}</dd></dl>
          ${rol() === 'ADMIN' ? '<div class="acciones"><button id="reconstruir" class="sec">Reconstruir proyección</button></div>' : ''}</div>
        <div class="tarjeta"><h2>Reconciliación</h2><dl class="kv"><dt>Resultado</dt><dd>${ind(rec.resultado)}</dd>
          <dt>Estado ≠ último evento</dt><dd>${rec.descuadres_estado_historial.length}</dd>
          <dt>Evidencias pendientes</dt><dd>${rec.evidencias_pendientes === null ? 'MongoDB no disponible' : rec.evidencias_pendientes.length}</dd></dl></div>
        <div class="tarjeta"><h2>Estado temporal (Redis)</h2><table><tr><th>Repartidor</th><th>Posición</th></tr>
          ${temporal.map((t) => `<tr><td>${esc(t.nombre)}</td><td>${t.posicion.vigente ? `<span class="ok-t">vigente</span> · expira en ${t.posicion.expira_en_seg}s` : `<span class="pequeno">${esc(t.posicion.mensaje)}</span>`}</td></tr>`).join('')}</table></div>
      </div>`;
    $('#refrescar').onclick = (ev) => { ev.preventDefault(); enrutar(); };
    const b = $('#reconstruir');
    if (b) b.onclick = async () => { try { const r = await api('/admin/sync/reconstruir', { metodo: 'POST' }); aviso(r.mensaje); enrutar(); } catch (e) { aviso(mensajeError(e), 'error'); } };
  },
};

window.addEventListener('hashchange', enrutar);
enrutar();
