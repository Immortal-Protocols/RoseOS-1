// Only these native provider operations have been inspected for headless use.
// This is independent of catalog foregroundRequired: that flag is not a safety
// contract (Settings open_panel is false but calls Context.startActivity).
const safeOperations = new Map([
 // pixel-calculator-agent/.../AgentProvider.java: CalculatorSession persists
 // results; refreshFromSession updates an existing activity without opening one.
 ['dev.pixel.calculator.agent', ['evaluate', 'continue', 'get_state', 'history_list', 'history_recall']],
 // rose-agent/.../SettingsAgentProvider.java: system settings/torch APIs only.
 // YouTubeAgent.state reads the media session; other youtube_* inspect the
 // default display through PhoneControl and cannot be used offscreen.
 ['dev.pixel.voice', ['get_state', 'brightness', 'rotation', 'flashlight', 'youtube_state',
  'install_approval_prepare', 'install_approval_finish', 'install_approval_status',
  'install_approval_cancel', 'install_approval_configure']],
 // pixel-weather/src/.../AgentProvider.java: network/cache/widget data only.
 ['dev.pixel.weather', ['find_city', 'current', 'forecast']],
 // Contacts/Gallery SuiteAgentProvider.execute: ContentResolver reads. Their
 // *_open operations call startActivity and must use background UI instead.
 ['com.android.contacts', ['contacts_search', 'contacts_get']],
 ['com.android.gallery3d', ['gallery_list_albums', 'gallery_search', 'gallery_get_media']],
 // Thunderbird agent/PixelMailOperations.kt: local store and messaging engine;
 // only mail_open_message invokes PixelMailUi. Existing send journals remain
 // authoritative: background routing adds no retries or sending permission.
 ['dev.pixel.mail', ['mail_list_accounts', 'mail_list_folders', 'mail_find_messages',
  'mail_read_message', 'mail_save_draft', 'mail_send_message', 'mail_send_status']],
 // Fossify calendar/agent/AgentProvider.kt: provider/store and GoogleSync;
 // only calendar_open_event invokes AgentUi.openEvent.
 ['org.fossify.calendar.pixel', ['calendar_list_calendars', 'calendar_find_events',
  'calendar_read_event', 'calendar_create_event', 'calendar_update_event',
  'calendar_delete_event', 'calendar_sync']],
].map(([pkg, operations]) => [pkg, new Set(operations)]));

// These providers' capabilities branches only read bundled tool definitions.
// No blanket capability exemption: an unknown provider is still unreviewed.
const capabilityPackages = new Set([
 ...safeOperations.keys(), 'dev.pixel.notes.agent', 'dev.pixel.books',
 'dev.pixel.podcasts', 'dev.pixel.reminders', 'dev.pixel.compass',
]);

// Intentionally excluded: Notes/Books depend on MainActivity.instance;
// Reminders starts an activity before every operation; Podcasts search,
// episodes, episode and queue launch activities; Compass opens MainActivity.
// Cameras, Feeder, Sky and Memories require separate display-aware review.
// Do not add operations based on their name or foregroundRequired metadata.
export function isBackgroundSafeAppCommand(command) {
 if (command?.action !== 'app_command' || typeof command.packageName !== 'string' ||
     typeof command.operation !== 'string') return false;
 return command.operation === 'capabilities'
  ? capabilityPackages.has(command.packageName)
  : safeOperations.get(command.packageName)?.has(command.operation) === true;
}

// Input/output matches app-tools-trial.json: {[packageName]: {status, tools}}.
// This filters native catalogs only. Locally implemented UI adapters must stay
// registered separately and send all their phone commands through the router.
export function filterBackgroundNativeCatalogs(catalogs) {
 if (!catalogs || typeof catalogs !== 'object' || Array.isArray(catalogs))
  throw new TypeError('Native catalogs must be an object keyed by package name');
 const filtered = structuredClone(catalogs);
 for (const [packageName, catalog] of Object.entries(filtered)) {
  if (!catalog || !Array.isArray(catalog.tools))
   throw new TypeError('Native catalog tools must be an array: ' + packageName);
  catalog.tools = catalog.tools.filter(spec => isBackgroundSafeAppCommand({
   action: 'app_command', packageName, operation: spec?.operation,
  })).map(spec => {
   const {launchPackage, ...headless} = spec;
   return {...headless, foregroundRequired: false};
  });
 }
 return filtered;
}

export function backgroundAppCommandRejection(command) {
 return {
  status: 'not_executed',
  packageName: command?.packageName ?? null,
  operation: command?.operation ?? null,
  reason: 'This native app operation has not been verified for background execution. Use the existing generic background phone UI controls to open the app, inspect its background screen, perform the action, and verify the result.',
  fallback: 'background_ui',
  nativeOperationDispatched: false,
 };
}
