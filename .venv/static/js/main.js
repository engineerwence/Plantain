// ─── AUTO DISMISS ALERTS ──────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {
    setTimeout(function () {
        document.querySelectorAll('.alert-dismissible').forEach(function (alert) {
            const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
            bsAlert.close();
        });
    }, 5000);
});

// ─── ACTIVE NAV LINK ──────────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {
    const currentPath = window.location.pathname;
    document.querySelectorAll('.nav-link').forEach(function (link) {
        if (link.getAttribute('href') === currentPath) {
            link.style.backgroundColor = 'rgba(255,255,255,0.2)';
            link.style.fontWeight = '700';
        }
    });
});

// ─── CONFIRM LOGOUT ───────────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {
    const logoutLink = document.querySelector('a[href*="logout"]');
    if (logoutLink) {
        logoutLink.addEventListener('click', function (e) {
            if (!confirm('Are you sure you want to logout?')) {
                e.preventDefault();
            }
        });
    }
});
```

That's all 15 files done! 🎉

Now let's test it. In your PyCharm terminal run:
```
python app.py