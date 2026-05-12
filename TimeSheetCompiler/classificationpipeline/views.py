from django.shortcuts import render
from django.shortcuts import render, redirect
# Create your views here.

def event_compare(request):
    if request.method == 'POST':
        calendar_ids = request.POST.getlist('calendars')
        print(calendar_ids)
        return render(request, 'classificationpipeline/event_compare.html', {'calendar_ids': calendar_ids})
    else:
        return redirect('calendars:eventi_calendars')