// A form is sent once: a second click or Enter while the page is loading is ignored.
// (Buttons are not disabled, so the clicked button's name/value still goes with the form.)
document.addEventListener('submit', function (e) {
  var form = e.target;
  if (form.method.toLowerCase() !== 'post') return;
  if (form.dataset.sent) {
    e.preventDefault();
    return;
  }
  form.dataset.sent = '1';
  form.classList.add('sending');
});
// Coming back with the browser's Back button: allow sending again
window.addEventListener('pageshow', function () {
  document.querySelectorAll('form[data-sent]').forEach(function (f) {
    delete f.dataset.sent;
    f.classList.remove('sending');
  });
});
