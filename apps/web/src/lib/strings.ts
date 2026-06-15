/**
 * Spanish UI strings — rioplatense register, plain text.
 *
 * Module 010 / Task T-001.
 *
 * All UI copy lives here so future translation passes and tone tweaks
 * stay surgical. Identifiers are English (constitution Gate 1); only
 * the values are Spanish.
 *
 * Grouped by surface area:
 *   - `appShell` — top bar + bottom nav
 *   - `today` — the daily chapter screen
 *   - `vote` — voting screen
 *   - `me` — user's twists screen
 *   - `settings` — settings screen
 *   - `install` — Android + iOS install prompts
 *   - `errors` — global error boundary + client-log surfaces
 *   - `states` — labels for cycle_state badges + waiting screens
 *
 * Usage:
 *   import { S } from '$lib/strings';
 *   <h1>{S.today.title}</h1>
 */

export const S = {
  appShell: {
    appName: 'AI Plot Twist',
    nav: {
      today: 'Hoy',
      vote: 'Votar',
      me: 'Mis ideas',
      settings: 'Ajustes',
    },
  },

  today: {
    title: 'El capítulo de hoy',
    cliffhanger: 'Y entonces…',
    submitTwistCta: 'Proponé tu giro',
    listenNarration: 'Escuchar narración',
  },

  vote: {
    title: 'Elegí qué giro quema todo',
    empty: 'Por ahora no hay propuestas para votar.',
    quotaReached: 'Llegaste al límite de votos por capítulo.',
    voteCta: 'Voto',
    unvoteCta: 'Sacar voto',
  },

  me: {
    title: 'Mis ideas',
    empty: 'Todavía no propusiste ningún giro.',
    quotaLeft: (used: number, max: number) =>
      `Llevás ${used} de ${max} propuestas en este capítulo.`,
    deleteCta: 'Borrar',
    deleteConfirm: '¿Seguro que querés borrarla?',
  },

  settings: {
    title: 'Ajustes',
    displayName: 'Tu nombre en la app',
    inviteCode: 'Código de invitación',
    inviteCodeMasked: (last4: string) => `••••-${last4}`,
    notifications: 'Notificaciones',
    notificationsHint: 'Avisarte cuando estrena un capítulo nuevo.',
    signOut: 'Cerrar sesión',
    signOutConfirm: 'Si cerrás sesión vas a tener que ingresar el código de invitación otra vez.',
    appVersion: (v: string) => `Versión ${v}`,
  },

  push: {
    enable: 'Activar',
    disable: 'Desactivar',
    saving: 'Guardando…',
    blocked: 'Bloqueadas',
  },

  install: {
    androidTitle: 'Sumá AI Plot Twist a tu inicio',
    androidBody: 'Andá a la app cuando se estrena el capítulo, en un toque.',
    androidCta: 'Instalar',
    androidDismiss: 'Ahora no',
    iosTitle: 'Agregalo al inicio',
    iosStep1: '1. Tocá el botón Compartir.',
    iosStep2: '2. Elegí "Agregar a pantalla de inicio".',
    iosDismiss: 'Listo',
  },

  errors: {
    boundaryTitle: 'Se rompió algo, perdón.',
    boundaryBody: 'Probá refrescar. Si sigue, contanos y lo miramos.',
    boundaryRetry: 'Refrescar',
    offlineBanner: 'Estás sin conexión. Te mostramos lo último que cargaste.',
    networkError: 'No pudimos contactar el servidor.',
  },

  states: {
    estreno: 'Recién estrenado',
    recepcionIdeas: 'Mandando ideas',
    filtering: 'El director está mirando las propuestas…',
    votacion: 'Votación abierta',
    generacion: 'Estamos generando el próximo capítulo…',
    pendingRelease: 'Calmá los bidones, ya viene lo nuevo.',
    failed: 'Algo no salió. Volvé en un rato.',
    maintenance: 'Estamos arreglando cosas. Ya volvemos.',
    noSeason: 'Pronto arranca la primera temporada.',
    firstReleaseHint: (when: string) => `Estreno: ${when}`,
  },
} as const;

/** Shape of the strings tree — useful for tests + typed access. */
export type Strings = typeof S;
