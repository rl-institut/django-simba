**To run the server, type from src:**

`
`

to migrate from src  
`python manage.py migrate`

create admin account via
`python manage.py createsuperuser`

to add an app called "myapp" type

`python manage.py startapp myapp `

implement the model in myapp similar to


`# Create your models here.
class myApp(models.Model):
    description = models.TextField()`


now you have to add the app name to list INSTALLED_APPS in the settings.py in ebusdjango 
`INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # custom apps
    'myapp',
]`

now run 
`python manage.py makemigrations`

and
`python manage.py migrate`

these last 2 commands have to be run in conjunction whenever models.py is changed

now register your app in admin.py of your "myapp" folder

`# Register your models here.
from .models import Calculator
admin.site.register(myApp)`

to get into the shell mode of you server type
python manage.py shell

import your model via
from myapp.models import myApp

check which data is in your database via
myApp.objects.all()

you can also create objects via you myApp constructor via and passing all the model attributes 
myapp.objects.create(description="foo" ...)

to create your own views/ pages go to
create an app called pages. The order of things is important
python manage.py startapp pages
add pages to INSTALLED_APPS in settings.py

go to views.py in your pages folder, that got created
from django.shortcuts import render
from django.http import HttpResponse
# Create your views here.
def home_view(*args, **kwargs):
    return HttpResponse("<h1> Hello World </h1>")

to reach this page it needs to  be added to urlpatterns in urls.py
from pages.views import home_view

urlpatterns = [
    path('', home_view, name='home'),
    path('admin/', admin.site.urls),

]

make sure to give the function and not the function_call (eg. function and not function())
also make sure to have that python knows pages is part of your sources

make use of django templating to get a proper view httpResponse 

from django.shortcuts import render
from django.http import HttpResponse
# Create your views here.
def home_view(request, *args, **kwargs):
    print(request.user)
    return render(request, "home.html", {})

def contact_view(*args, **kwargs):
    return HttpResponse("<h1> Contacts </h1>")

create folder named templates and put your html templates inside
add templates path to TEMPLATES in settings.py

to start celery worker
$ celery -A ebusdjango worker --loglevel=info
