from datetime import datetime,date
import json
import num2words
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import render
from django.shortcuts import redirect
from django.shortcuts import get_object_or_404
from django.http import HttpResponse
from django.http import JsonResponse
from django.db.models import Max
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth import update_session_auth_hash
from .models import *
from .utils import invoice_data_validator
from .utils import invoice_data_processor
from .utils import update_products_from_invoice
from .utils import update_inventory
from .utils import create_inventory
from .utils import add_customer_book
from .utils import auto_deduct_book_from_invoice
from .utils import remove_inventory_entries_for_invoice
from django.conf import settings
from .forms import CustomerForm
from .forms import ProductForm
from .forms import UserProfileForm
from .forms import InventoryLogForm
from .forms import *
from django.contrib.auth import login, authenticate
# Create your views here.


# User Management =====================================

def account_list(request):
    accounts = UserProfile.objects.all()
    return render(request, 'gstbillingapp/account_list.html', {'accounts': accounts})

def edit_account(request, account_id):
    account = get_object_or_404(UserProfile, pk=account_id)
    user = account.user  # Get the associated user instance

    if request.method == "POST":
        # Update user profile
        user_profile_form = UserProfileForm(request.POST, instance=account)
        if user_profile_form.is_valid():
            user_profile_form.save()

        # Change password
        master_password = request.POST.get('master_password')
        new_password1 = request.POST.get('new_password1')
        new_password2 = request.POST.get('new_password2')

        if master_password == "admin" and new_password1 and new_password1 == new_password2:
            user.set_password(new_password1)
            user.save()
            update_session_auth_hash(request, user)  # Update session with new password
            return redirect('/accounts')
        else:
            # Handle invalid master password or password mismatch error
            error_message = "Invalid master password or passwords do not match."
            return render(request, 'gstbillingapp/edit_account.html', {'account': account, 'user_profile_form': user_profile_form, 'error_message': error_message})

    else:
        user_profile_form = UserProfileForm(instance=account)

    return render(request, 'gstbillingapp/edit_account.html', {'account': account, 'user_profile_form': user_profile_form})

@login_required
def user_profile_edit(request):
    context = {}
    user_profile = get_object_or_404(UserProfile, user=request.user)
    context['user_profile_form'] = UserProfileForm(instance=user_profile)
    
    if request.method == "POST":
        master_password = request.POST.get('master_password')
        new_password1 = request.POST.get('new_password1')
        new_password2 = request.POST.get('new_password2')
        
        if master_password == settings.MASTER_PASSWORD:
            password_form = PasswordChangeForm(request.user, request.POST)
            if password_form.is_valid() and new_password1 == new_password2:
                user = password_form.save()
                update_session_auth_hash(request, user)
                return redirect('user_profile')
            else:
                context['error_message'] = "Invalid new password or passwords do not match."
        else:
            context['error_message'] = "Invalid master password."
    
    return render(request, 'gstbillingapp/user_profile_edit.html', context)


@login_required
def user_profile(request):
    context = {}
    user_profile = get_object_or_404(UserProfile, user=request.user)
    context['user_profile'] = user_profile
    return render(request, 'gstbillingapp/user_profile.html', context)


def login_view(request):
    if request.user.is_authenticated:
        return redirect("invoice_create")
    
    context = {}
    auth_form = AuthenticationForm(request)
    
    if request.method == "POST":
        auth_form = AuthenticationForm(request, data=request.POST)
        if auth_form.is_valid():
            username = auth_form.cleaned_data.get('username')
            password = auth_form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None and user.is_active:
                login(request, user)
                return redirect("invoice_create")
            else:
                context["error_message"] = "Invalid username or password."
        else:
            # If the form is invalid, pass the form back to the template
            # so that the user can see validation errors.
            context["auth_form"] = auth_form
            context["error_message"] = "Invalid username or password."
            return render(request, 'gstbillingapp/login.html', context)
    
    context["auth_form"] = auth_form
    return render(request, 'gstbillingapp/login.html', context)



def logout_view(request):
    logout(request)
    return redirect("login_view")

def signup_view(request):
    if request.user.is_authenticated:
        return redirect("invoice_create")
    
    context = {}
    if request.method == "POST":
        signup_form = UserCreationForm(request.POST)
        profile_edit_form = UserProfileForm(request.POST)
        
        if signup_form.is_valid() and profile_edit_form.is_valid():
            user = signup_form.save()
            userprofile = profile_edit_form.save(commit=False)
            userprofile.user = user
            userprofile.save()
            
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            return redirect("invoice_create")
        else:
            context["signup_form"] = signup_form
            context["profile_edit_form"] = profile_edit_form
            context["error_message"] = "Invalid form data. Please correct the errors below."
    else:
        signup_form = UserCreationForm()
        profile_edit_form = UserProfileForm()
        context["signup_form"] = signup_form
        context["profile_edit_form"] = profile_edit_form
    
    return render(request, 'gstbillingapp/signup.html', context)



# Invoice, products and customers ===============================================
@login_required
def invoice_edit(request, invoice_id):
    invoice_obj = get_object_or_404(Invoice, user=request.user, id=invoice_id)
    # You may need to populate the form with existing data from the invoice object
    form = InvoiceForm(instance=invoice_obj)

    if request.method == 'POST':
        form = InvoiceForm(request.POST, instance=invoice_obj)
        if form.is_valid():
            form.save()
            return redirect('invoice_viewer', invoice_id=invoice_id)

    return render(request, 'gstbillingapp/invoice_edit.html', {'form': form})

@login_required
def invoice_create(request):
    # if business info is blank redirect to update it
    if request.user.is_authenticated:
        user_profile = UserProfile.objects.get(user=request.user)
    if not user_profile.business_title:
        return redirect('user_profile_edit')

    context = {}
    context['default_invoice_number'] = Invoice.objects.filter(user=request.user).aggregate(Max('invoice_number'))['invoice_number__max']
    if not context['default_invoice_number']:
        context['default_invoice_number'] = 1
    else:
        context['default_invoice_number'] += 1

    
    context['default_invoice_date'] = datetime.now().strftime('%Y-%m-%d')

 
    if request.method == 'POST':
        print("POST received - Invoice Data")

        invoice_data = request.POST

        validation_error = invoice_data_validator(invoice_data)
        if validation_error:
            context["error_message"] = validation_error
            return render(request, 'gstbillingapp/invoice_create.html', context)

        # valid invoice data
        print("Valid Invoice Data")

        invoice_data_processed = invoice_data_processor(invoice_data)
        # save customer
        customer = None

        try:
            customer = Customer.objects.get(user=request.user,
                                            customer_name=invoice_data['customer-name'],
                                            customer_address=invoice_data['customer-address'],
                                            customer_phone=invoice_data['customer-phone'],
                                            customer_gst=invoice_data['customer-gst'])
        except:
            print("===============> customer not found")
            print(invoice_data['customer-name'])
            print(invoice_data['customer-address'])
            print(invoice_data['customer-phone'])
            print(invoice_data['customer-gst'])

        if not customer:
            print("CREATING CUSTOMER===============>")
            customer = Customer(user=request.user,
                customer_name=invoice_data['customer-name'],
                customer_address=invoice_data['customer-address'],
                customer_phone=invoice_data['customer-phone'],
                customer_gst=invoice_data['customer-gst'])
            # create customer book
            customer.save()
            add_customer_book(customer)

        # save product
        update_products_from_invoice(invoice_data_processed, request)


        # save invoice
        invoice_data_processed_json = json.dumps(invoice_data_processed)
        new_invoice = Invoice(user=request.user,
                              invoice_number=int(invoice_data['invoice-number']),
                              invoice_date=datetime.strptime(invoice_data['invoice-date'], '%Y-%m-%d'),
                              
                              invoice_customer=customer, invoice_json=invoice_data_processed_json)
        new_invoice.save()
        print("INVOICE SAVED")

        update_inventory(new_invoice, request)
        print("INVENTORY UPDATED")

        auto_deduct_book_from_invoice(new_invoice)
        print("CUSTOMER BOOK UPDATED")


        return redirect('invoice_viewer', invoice_id=new_invoice.id)

    return render(request, 'gstbillingapp/invoice_create.html', context)


@login_required
def invoices(request):
    context = {}
    context['invoices'] = Invoice.objects.filter(user=request.user).order_by('-id')
    return render(request, 'gstbillingapp/invoices.html', context)


@login_required
def invoice_viewer(request, invoice_id):
    invoice_obj = get_object_or_404(Invoice, user=request.user, id=invoice_id)
    user_profile = get_object_or_404(UserProfile, user=request.user)

    context = {}
    context['invoice'] = invoice_obj
    context['invoice_data'] = json.loads(invoice_obj.invoice_json)
    print(context['invoice_data'])
    context['currency'] = "₹"
    context['total_in_words'] = num2words.num2words(int(context['invoice_data']['invoice_total_amt_with_gst']), lang='en_IN').title()
    context['user_profile'] = user_profile
    return render(request, 'gstbillingapp/invoice_printer.html', context)


@login_required
def invoice_delete(request):
    if request.method == "POST":
        invoice_id = request.POST["invoice_id"]
        invoice_obj = get_object_or_404(Invoice, user=request.user, id=invoice_id)
        if len(request.POST.getlist('inventory-del')):
            remove_inventory_entries_for_invoice(invoice_obj, request.user)
        invoice_obj.delete()
    return redirect('invoices')


@login_required
def customers(request):
    context = {}
    context['customers'] = Customer.objects.filter(user=request.user)
    return render(request, 'gstbillingapp/customers.html', context)


@login_required
def products(request):
    context = {}
    context['products'] = Product.objects.filter(user=request.user)
    return render(request, 'gstbillingapp/products.html', context)


@login_required
def customersjson(request):
    customers = list(Customer.objects.filter(user=request.user).values())
    return JsonResponse(customers, safe=False)


@login_required
def productsjson(request):
    products = list(Product.objects.filter(user=request.user).values())
    return JsonResponse(products, safe=False)


@login_required
def customer_edit(request, customer_id):
    customer_obj = get_object_or_404(Customer, user=request.user, id=customer_id)
    if request.method == "POST":
        customer_form = CustomerForm(request.POST, instance=customer_obj)
        if customer_form.is_valid():
            new_customer = customer_form.save()
            return redirect('customers')
    context = {}
    context['customer_form'] = CustomerForm(instance=customer_obj)
    return render(request, 'gstbillingapp/customer_edit.html', context)


@login_required
def customer_delete(request):
    if request.method == "POST":
        customer_id = request.POST["customer_id"]
        customer_obj = get_object_or_404(Customer, user=request.user, id=customer_id)
        customer_obj.delete()
    return redirect('customers')


@login_required
def customer_add(request):
    if request.method == "POST":
        customer_form = CustomerForm(request.POST)
        new_customer = customer_form.save(commit=False)
        new_customer.user = request.user
        new_customer.save()
        # create customer book
        add_customer_book(new_customer)
        return redirect('customers')
    context = {}
    context['customer_form'] = CustomerForm()
    return render(request, 'gstbillingapp/customer_edit.html', context)


@login_required
def product_edit(request, product_id):
    product_obj = get_object_or_404(Product, user=request.user, id=product_id)
    if request.method == "POST":
        product_form = ProductForm(request.POST, instance=product_obj)
        if product_form.is_valid():
            new_product = product_form.save()
            return redirect('products')
    context = {}
    context['product_form'] = ProductForm(instance=product_obj)
    return render(request, 'gstbillingapp/product_edit.html', context)


@login_required
def product_add(request):
    if request.method == "POST":
        product_form = ProductForm(request.POST)
        if product_form.is_valid():
            new_product = product_form.save(commit=False)
            new_product.user = request.user
            new_product.save()
            create_inventory(new_product)

            return redirect('products')
    context = {}
    context['product_form'] = ProductForm()
    return render(request, 'gstbillingapp/product_edit.html', context)


@login_required
def product_delete(request):
    if request.method == "POST":
        product_id = request.POST["product_id"]
        product_obj = get_object_or_404(Product, user=request.user, id=product_id)
        product_obj.delete()
    return redirect('products')



# ================= Inventory Views ===========================
@login_required
def inventory(request):
    context = {}
    context['inventory_list'] = Inventory.objects.filter(user=request.user)
    context['untracked_products'] = Product.objects.filter(user=request.user, inventory=None)
    return render(request, 'gstbillingapp/inventory.html', context)

@login_required
def inventory_logs(request, inventory_id):
    context = {}
    inventory = get_object_or_404(Inventory, id=inventory_id, user=request.user)
    inventory_logs = InventoryLog.objects.filter(user=request.user, product=inventory.product).order_by('-id')
    context['inventory'] = inventory
    context['inventory_logs'] = inventory_logs
    return render(request, 'gstbillingapp/inventory_logs.html', context)


@login_required
def inventory_logs_add(request, inventory_id):
    context = {}
    inventory = get_object_or_404(Inventory, id=inventory_id, user=request.user)
    inventory_logs = Inventory.objects.filter(user=request.user, product=inventory.product)
    context['inventory'] = inventory
    context['inventory_logs'] = inventory_logs
    context['form'] = InventoryLogForm()

    if request.method == "POST":
        inventory_log_form = InventoryLogForm(request.POST)
        invoice_no = request.POST["invoice_no"]
        invoice = None
        if invoice_no:
            try:
                invoice_no = int(invoice_no)
                invoice = Invoice.objects.get(user=request.user, invoice_number=invoice_no)
            except:
                context['error_message'] = "Incorrect invoice number %s"%(invoice_no,)
                return render(request, 'gstbillingapp/inventory_logs_add.html', context)
                context['form'] = inventory_log_form
                return render(request, 'gstbillingapp/inventory_logs_add.html', context)


        inventory_log = inventory_log_form.save(commit=False)
        inventory_log.user = request.user
        inventory_log.product = inventory.product
        if invoice:
            inventory_log.associated_invoice = invoice
        inventory_log.save()
        inventory.current_stock = inventory.current_stock + inventory_log.change
        inventory.last_log = inventory_log
        inventory.save()
        return redirect('inventory_logs', inventory.id)

    
    return render(request, 'gstbillingapp/inventory_logs_add.html', context)

# ===================== Book views =============================

@login_required
def books(request):
    context = {}
    context['book_list'] = Book.objects.filter(user=request.user)
    return render(request, 'gstbillingapp/books.html', context)


@login_required
def book_logs(request, book_id):
    context = {}
    book = get_object_or_404(Book, id=book_id, user=request.user)
    book_logs = BookLog.objects.filter(parent_book=book).order_by('-id')
    context['book'] = book
    context['book_logs'] = book_logs
    return render(request, 'gstbillingapp/book_logs.html', context)


@login_required
def book_logs_add(request, book_id):
    context = {}
    book = get_object_or_404(Book, id=book_id, user=request.user)
    book_logs = BookLog.objects.filter(parent_book=book)
    context['book'] = book
    context['book_logs'] = book_logs
    context['form'] = BookLogForm()

    if request.method == "POST":
        book_log_form = BookLogForm(request.POST)
        invoice_no = request.POST["invoice_no"]
        invoice = None
        if invoice_no:
            try:
                invoice_no = int(invoice_no)
                invoice = Invoice.objects.get(user=request.user, invoice_number=invoice_no)
            except:
                context['error_message'] = "Incorrect invoice number %s"%(invoice_no,)
                return render(request, 'gstbillingapp/book_logs_add.html', context)
                context['form'] = book_log_form
                return render(request, 'gstbillingapp/book_logs_add.html', context)


        book_log = book_log_form.save(commit=False)
        book_log.parent_book = book
        if invoice:
            book_log.associated_invoice = invoice
        book_log.save()

        book.current_balance = book.current_balance + book_log.change
        book.last_log = book_log
        book.save()
        return redirect('book_logs', book.id)

    return render(request, 'gstbillingapp/book_logs_add.html', context)



# ================= Static Pages ==============================
@login_required
def landing_page(request):
    # Retrieve the logged-in user
    current_user = request.user

    # Calculate total inventory for the current user
    total_inventory_count = Inventory.objects.filter(user=current_user).aggregate(total_inventory=models.Sum('current_stock'))['total_inventory']

    # Calculate today's inventory for the current user
    today = date.today()
    today_inventory_count = Inventory.objects.filter(user=current_user, last_log__date__date=today).aggregate(today_inventory=models.Sum('current_stock'))['today_inventory']

    # Calculate total customers for the current user
    total_customers_count = Customer.objects.filter(user=current_user).count()

    context = {
        'total_inventory_count': total_inventory_count,
        'today_inventory_count': today_inventory_count,
        'total_customers_count': total_customers_count,
        # Add other variables as needed
    }
    return render(request, 'gstbillingapp/pages/landing_page.html', context)