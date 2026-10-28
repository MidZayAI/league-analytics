/* Assembles the Alpine app from the section modules. Property descriptors are copied (not
   spread) so getters stay live. */
function mixin(...parts) {
  const out = {};
  parts.forEach((p) => Object.defineProperties(out, Object.getOwnPropertyDescriptors(p)));
  return out;
}

document.addEventListener('alpine:init', () => {
  Alpine.data('app', () => mixin(window.Core, window.Legacy, window.Matchups, window.Teams, window.League, window.History, window.Odds, window.Injuries, window.Podcast));
});

