/* Shared bootstrap for both pages: theme + language. Loaded synchronously in
   <head> so the theme and <html lang> are set before the first paint. */

(function () {
  // ---- translations -----------------------------------------------------
  // English is the source language and the fallback for any missing key.
  var STRINGS = {
    en: {
      toggle_sheet: 'Collapse / expand panel',
      unknown: 'unknown',

      // poll-failure banner (both pages)
      poll_alert: 'Location updates are failing. Last successful update: {last}.',
      poll_alert_stale: 'No location update in a long time. Last successful update: {last}.',

      // index page
      app_title: 'FindMy Map',
      loading: 'Loading…',
      refresh: 'Refresh now',
      refreshing: 'Refreshing…',
      timeline_link: 'Timeline →',
      edit: 'Edit',
      ring: 'Ring',
      stop_ring: 'Stop ringing',
      at_place: '{place} · {time}',
      last_poll: 'Last poll',
      never: 'none yet',
      error: 'Error',
      server_unreachable: 'Server unreachable',
      devices_without_location: '{n} device(s) without a current location',
      err_no_response: 'no response',
      err_fetch_failed: 'fetch failed',
      err_decryption_failed: 'decryption failed',
      err_no_location_data: 'no location data',
      err_no_location: 'no location',
      p_time: 'Time',
      p_accuracy: 'Accuracy',
      p_status: 'Status',
      p_own_report: 'Own report',
      yes: 'yes',
      no: 'no',
      f_name: 'Name',
      f_pin_color: 'Pin colour',
      f_group: 'Group',
      ungrouped: 'Ungrouped',
      save: 'Save',
      cancel: 'Cancel',
      reset_default: 'Default',

      // timeline page
      page_title_timeline: 'FindMy Map – Timeline',
      timeline_heading: 'Timeline',
      back_to_map: '← Back to map',
      f_device: 'Device',
      f_from: 'From',
      f_to: 'To',
      range_day: 'Day',
      range_week: 'Week',
      range_month: 'Month',
      range_custom: 'Range',
      range_prev: 'Previous',
      range_next: 'Next',
      visits_show_more: 'Show {n} more',
      show: 'Show',
      loading_short: 'Loading…',
      visited_places: 'Visited places',
      no_devices: 'No devices found.',
      history_load_error: 'Failed to load the history.',
      no_points_in_range: 'No locations in the selected period.',
      locations_count: '{n} locations',
      start: 'Start',
      end: 'End',
      resolving_address: 'Resolving address…',
      geocoding_disabled: 'Address lookup disabled (GFM_NOMINATIM_URL is empty).',
      no_visits: 'No stays (at least 15 min in one place) in the selected period. '
        + 'Once more location data has accumulated, visited places will appear here.',
      unit_min: 'min',
      unit_hour: 'h',
      visit_delete: 'Delete this visit',
      visit_confirm_q: 'Permanently delete the visit to "{place}" ({when}, {n} points)? '
        + 'This also shortens the track for that period — this cannot be undone.',
      visit_confirm_go: 'Delete',
      s_export: 'Data export',
      export_track: 'Track',
      export_visits: 'Visited places',
      export_hint_full: 'The complete history for this device (GPX / GeoJSON / CSV).',

      // login page
      page_title_login: 'FindMy Map – Sign in',
      login_heading: 'FindMy Map',
      f_username: 'Username',
      f_password: 'Password',
      sign_in: 'Sign in',
      login_wrong: 'Wrong password.',
      login_throttled: 'Too many attempts — try again in {n} s.',
      login_error: 'Something went wrong. Try again.',

      // settings page
      settings: 'Settings',
      page_title_settings: 'FindMy Map – Settings',
      settings_heading: 'Settings',
      s_language: 'Language',
      s_theme: 'Theme',
      s_auth: 'Authentication',
      theme_light: 'Light',
      theme_dark: 'Dark',
      require_login: 'Require login',
      f_current_password: 'Current password',
      f_new_password: 'Password',
      f_new_password_optional: 'New password (leave blank to keep)',
      f_confirm_password: 'Confirm password',
      log_out: 'Log out',
      auth_saved_on: 'Saved. A login is now required.',
      auth_saved_off: 'Saved. Login is no longer required.',
      err_pw_short: 'Password must be at least 8 characters.',
      err_pw_mismatch: 'The passwords do not match.',
      err_current_pw: 'Current password is incorrect.',
      err_username_required: 'Username is required.',
      s_devices: 'Old devices',
      dev_stale_hint: 'Devices that no longer appear on the map. Deleting one removes its whole history and settings — it cannot be undone.',
      dev_never_seen: 'never seen',
      dev_points: '{n} points',
      dev_delete: 'Delete',
      dev_confirm_q: 'Permanently delete “{name}” and its {n} location points? Check the id below — this cannot be undone.',
      dev_confirm_go: 'Delete',
      dev_delete_active: 'Device is active again — refreshing the list.',
      dev_none: 'No old devices — every device is still active.',
    },
    de: {
      toggle_sheet: 'Panel ein-/ausklappen',
      unknown: 'unbekannt',

      poll_alert: 'Standortabruf schlägt fehl. Letzte erfolgreiche Aktualisierung: {last}.',
      poll_alert_stale: 'Seit Langem keine Standortaktualisierung. Letzte erfolgreiche Aktualisierung: {last}.',

      app_title: 'FindMy Map',
      loading: 'Lade…',
      refresh: 'Jetzt aktualisieren',
      refreshing: 'Aktualisiere…',
      timeline_link: 'Zeitachse →',
      edit: 'Bearbeiten',
      ring: 'Klingeln lassen',
      stop_ring: 'Klingeln stoppen',
      at_place: '{place} · {time}',
      last_poll: 'Letzte Abfrage',
      never: 'noch keine',
      error: 'Fehler',
      server_unreachable: 'Server nicht erreichbar',
      devices_without_location: '{n} Gerät(e) ohne aktuellen Standort',
      err_no_response: 'keine Antwort',
      err_fetch_failed: 'Abruf fehlgeschlagen',
      err_decryption_failed: 'Entschlüsselung fehlgeschlagen',
      err_no_location_data: 'keine Standortdaten',
      err_no_location: 'kein Standort',
      p_time: 'Zeit',
      p_accuracy: 'Genauigkeit',
      p_status: 'Status',
      p_own_report: 'Eigener Report',
      yes: 'ja',
      no: 'nein',
      f_name: 'Name',
      f_pin_color: 'Pin-Farbe',
      f_group: 'Gruppe',
      ungrouped: 'Ohne Gruppe',
      save: 'Speichern',
      cancel: 'Abbrechen',
      reset_default: 'Standard',

      page_title_timeline: 'FindMy Map – Zeitachse',
      timeline_heading: 'Zeitachse',
      back_to_map: '← Zurück zur Karte',
      f_device: 'Gerät',
      f_from: 'Von',
      f_to: 'Bis',
      range_day: 'Tag',
      range_week: 'Woche',
      range_month: 'Monat',
      range_custom: 'Zeitraum',
      range_prev: 'Zurück',
      range_next: 'Weiter',
      visits_show_more: '{n} weitere anzeigen',
      show: 'Anzeigen',
      loading_short: 'Lädt…',
      visited_places: 'Besuchte Orte',
      no_devices: 'Keine Geräte gefunden.',
      history_load_error: 'Fehler beim Laden des Verlaufs.',
      no_points_in_range: 'Keine Standorte im gewählten Zeitraum.',
      locations_count: '{n} Standorte',
      start: 'Start',
      end: 'Ende',
      resolving_address: 'Adresse wird ermittelt…',
      geocoding_disabled: 'Adressen deaktiviert (GFM_NOMINATIM_URL leer).',
      no_visits: 'Keine Aufenthalte (mind. 15 Min am selben Ort) im gewählten Zeitraum. '
        + 'Sobald sich mehr Standortdaten angesammelt haben, erscheinen hier die besuchten Orte.',
      unit_min: 'Min',
      unit_hour: 'Std',
      visit_delete: 'Diesen Besuch löschen',
      visit_confirm_q: 'Den Besuch bei „{place}" ({when}, {n} Punkte) endgültig löschen? '
        + 'Dadurch wird auch der Track für diesen Zeitraum verkürzt — das lässt sich nicht rückgängig machen.',
      visit_confirm_go: 'Löschen',
      s_export: 'Datenexport',
      export_track: 'Track',
      export_visits: 'Besuchte Orte',
      export_hint_full: 'Der komplette Verlauf dieses Geräts (GPX / GeoJSON / CSV).',

      // login page
      page_title_login: 'FindMy Map – Anmelden',
      login_heading: 'FindMy Map',
      f_username: 'Benutzername',
      f_password: 'Passwort',
      sign_in: 'Anmelden',
      login_wrong: 'Falsches Passwort.',
      login_throttled: 'Zu viele Versuche — in {n} s erneut probieren.',
      login_error: 'Etwas ist schiefgelaufen. Bitte erneut versuchen.',

      // settings page
      settings: 'Einstellungen',
      page_title_settings: 'FindMy Map – Einstellungen',
      settings_heading: 'Einstellungen',
      s_language: 'Sprache',
      s_theme: 'Design',
      s_auth: 'Authentifizierung',
      theme_light: 'Hell',
      theme_dark: 'Dunkel',
      require_login: 'Login erforderlich',
      f_current_password: 'Aktuelles Passwort',
      f_new_password: 'Passwort',
      f_new_password_optional: 'Neues Passwort (leer lassen zum Behalten)',
      f_confirm_password: 'Passwort bestätigen',
      log_out: 'Abmelden',
      auth_saved_on: 'Gespeichert. Ein Login ist jetzt erforderlich.',
      auth_saved_off: 'Gespeichert. Kein Login mehr erforderlich.',
      err_pw_short: 'Das Passwort muss mindestens 8 Zeichen haben.',
      err_pw_mismatch: 'Die Passwörter stimmen nicht überein.',
      err_current_pw: 'Aktuelles Passwort ist falsch.',
      err_username_required: 'Benutzername ist erforderlich.',
      s_devices: 'Alte Geräte',
      dev_stale_hint: 'Geräte, die nicht mehr auf der Karte erscheinen. Beim Löschen werden der gesamte Verlauf und die Einstellungen entfernt — das lässt sich nicht rückgängig machen.',
      dev_never_seen: 'nie gesehen',
      dev_points: '{n} Punkte',
      dev_delete: 'Löschen',
      dev_confirm_q: '„{name}“ mit {n} Standortpunkten endgültig löschen? Prüfe die ID unten — das lässt sich nicht rückgängig machen.',
      dev_confirm_go: 'Löschen',
      dev_delete_active: 'Gerät ist wieder aktiv — Liste wird aktualisiert.',
      dev_none: 'Keine alten Geräte — alle Geräte sind noch aktiv.',
    },
  };

  function readLang() {
    try {
      var v = localStorage.getItem('lang');
      if (v === 'en' || v === 'de') return v;
    } catch (e) {}
    return 'en';
  }

  var lang = readLang();

  function t(key, vars) {
    var s = (STRINGS[lang] && STRINGS[lang][key]) || STRINGS.en[key] || key;
    if (vars) {
      for (var k in vars) s = s.replace('{' + k + '}', vars[k]);
    }
    return s;
  }

  function locale() { return lang === 'de' ? 'de-DE' : 'en-GB'; }

  function applyStaticI18n() {
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      el.textContent = t(el.getAttribute('data-i18n'));
    });
    document.querySelectorAll('[data-i18n-aria]').forEach(function (el) {
      el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria')));
    });
    var titleEl = document.querySelector('title[data-i18n]');
    if (titleEl) document.title = t(titleEl.getAttribute('data-i18n'));
  }

  // ---- theme ----------------------------------------------------------
  try {
    var savedTheme = localStorage.getItem('theme');
    if (savedTheme === 'light' || savedTheme === 'dark') {
      document.documentElement.dataset.theme = savedTheme;
    }
  } catch (e) {}

  function currentTheme() {
    return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('theme', theme); } catch (e) {}
  }

  function setLang(next) {
    lang = next;
    try { localStorage.setItem('lang', next); } catch (e) {}
    document.documentElement.lang = next;
    applyStaticI18n();
    if (typeof window.FindMyMap.onLangChange === 'function') {
      window.FindMyMap.onLangChange(next);
    }
  }

  // ---- poll-failure banner (shared by index.html + timeline.html) -----
  // `info` is the /api/locations payload on index (has last_error) or the
  // /api/health payload on timeline (no error string). Both carry
  // poll_alert / poll_stale / last_poll.
  function renderPollAlert(info) {
    var el = document.getElementById('poll-alert');
    if (!el) return;
    info = info || {};
    var alert = info.poll_alert === true;
    var stale = info.poll_stale === true;
    var root = document.documentElement;
    if (!alert && !stale) {
      el.hidden = true;
      root.classList.remove('has-poll-alert');
      return;
    }
    var last = info.last_poll
      ? new Date(info.last_poll * 1000).toLocaleString(locale())
      : t('never');
    var msg = t(stale && !alert ? 'poll_alert_stale' : 'poll_alert', { last: last });
    if (info.last_error) msg += ' (' + String(info.last_error).slice(0, 200) + ')';
    el.textContent = msg;   // textContent -- last_error is never treated as HTML
    el.title = msg;         // full text on hover; the bar itself truncates
    el.hidden = false;
    root.classList.add('has-poll-alert');
  }

  // ---- public API + wiring ------------------------------------------
  window.FindMyMap = window.FindMyMap || {};
  window.FindMyMap.t = t;
  window.FindMyMap.locale = locale;
  window.FindMyMap.renderPollAlert = renderPollAlert;
  window.FindMyMap.getLang = function () { return lang; };
  window.FindMyMap.onThemeChange = null;
  window.FindMyMap.onLangChange = null;
  window.FindMyMap.setLang = setLang;
  window.FindMyMap.getTheme = currentTheme;
  window.FindMyMap.setTheme = function (theme) {
    applyTheme(theme === 'light' ? 'light' : 'dark');
    if (typeof window.FindMyMap.onThemeChange === 'function') {
      window.FindMyMap.onThemeChange(theme);
    }
  };

  document.documentElement.lang = lang;

  function setup() {
    applyStaticI18n();
    applyTheme(currentTheme());
  }

  if (document.readyState !== 'loading') setup();
  else document.addEventListener('DOMContentLoaded', setup);
})();
