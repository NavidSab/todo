در این پروژه نمونه Laravel، یک API سیستم Todo با جزییات زیر پیاده‌سازی می‌کنیم:

پیاده‌سازی احراز هویت از طریق ایمیل و رمز عبور: کاربران می‌توانند با استفاده از ایمیل و رمز عبور خود ثبت نام کرده و وارد سیستم شوند.
ایجاد تسک نامحدود: هر کاربر می‌تواند تعداد نامحدودی تسک ایجاد کند.
تعیین وضعیت تسک: هر تسک می‌تواند دو وضعیت داشته باشد: "در حال انجام" و "انجام شده".
پخش وضعیت تسک: با تغییر وضعیت یک تسک، وضعیت آن از طریق websockets به تمام دستگاه‌های متصل به کاربر مربوطه پخش می‌شود.
انجام خودکار تسک‌های معوق: تسک‌هایی که دو روز از زمان ایجاد آنها می‌گذرد، به طور خودکار در پس‌زمینه به عنوان "انجام شده" علامت‌گذاری می‌شوند.