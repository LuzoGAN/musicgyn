// GymBeats - interações
document.addEventListener('DOMContentLoaded', function () {
  // Auto-hide flash messages
  document.querySelectorAll('.flash').forEach(function (el) {
    setTimeout(function () {
      el.style.transition = 'opacity .3s';
      el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 320);
    }, 5000);
  });

  // Countdown das votações
  function tick() {
    document.querySelectorAll('[data-countdown]').forEach(function (el) {
      var end = new Date(el.getAttribute('data-countdown'));
      var diff = end - new Date();
      if (isNaN(end)) { el.textContent = ''; return; }
      if (diff <= 0) { el.textContent = 'Encerrada'; return; }
      var h = Math.floor(diff / 3600000);
      var m = Math.floor((diff % 3600000) / 60000);
      var s = Math.floor((diff % 60000) / 1000);
      el.textContent = h + 'h ' + m + 'm ' + s + 's';
    });
  }
  tick();
  setInterval(tick, 1000);
});

function confirmAction(msg) {
  return confirm(msg || 'Tem certeza?');
}
