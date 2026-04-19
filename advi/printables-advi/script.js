// ===== Product Data & Cart State =====
const products = [
    { id: 'planner-bundle', name: 'Planner Templates Bundle', price: 24.99 },
    { id: 'social-templates', name: 'Social Media Templates', price: 19.99 },
    { id: 'business-cards', name: 'Business Card Designs', price: 14.99 },
    { id: 'logo-pack', name: 'Logo Pack Collection', price: 29.99 },
    { id: 'presentation-templates', name: 'Presentation Templates', price: 17.99 },
    { id: 'resume-templates', name: 'Resume & CV Templates', price: 12.99 }
];

let cart = [];

// ===== DOM Elements =====
const addToCartButtons = document.querySelectorAll('.add-to-cart');
const cartItemsContainer = document.getElementById('cart-items');
const cartTotalElement = document.getElementById('cart-total');
const checkoutForm = document.getElementById('checkout-form');
const cardDetailsSection = document.getElementById('card-details');

// ===== Toast Notification =====
function showToast(message, duration = 3000) {
    const toast = document.getElementById('toast');
    const toastMessage = toast.querySelector('.toast-message');

    toastMessage.textContent = message;
    toast.classList.remove('hidden');
    toast.classList.add('show');

    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.classList.add('hidden'), 300);
    }, duration);
}

// ===== Add to Cart Function =====
addToCartButtons.forEach(button => {
    button.addEventListener('click', function() {
        const productId = this.getAttribute('data-product-id');
        const product = products.find(p => p.id === productId);

        if (product) {
            cart.push(product);
            updateCartDisplay();
            showToast(`✓ ${product.name} added to cart!`);

            // Animate the button
            this.textContent = '✓ Added';
            setTimeout(() => this.textContent = 'Add to Cart', 1500);
        }
    });
});

// ===== Update Cart Display =====
function updateCartDisplay() {
    if (cart.length === 0) {
        cartItemsContainer.innerHTML = '<p class="empty-cart">Your cart is empty. Add products from our collection to get started!</p>';
        cartTotalElement.textContent = '$0.00';
        return;
    }

    let total = 0;
    cartItemsContainer.innerHTML = '';

    cart.forEach((item, index) => {
        const itemElement = document.createElement('div');
        itemElement.className = 'cart-item';
        itemElement.innerHTML = `
            <span>${item.name}</span>
            <span>$${item.price.toFixed(2)}</span>
            <button class="remove-item" data-index="${index}" aria-label="Remove ${item.name}">✕</button>
        `;

        // Add remove functionality
        itemElement.querySelector('.remove-item').addEventListener('click', function() {
            cart.splice(index, 1);
            updateCartDisplay();
            showToast(`✓ ${item.name} removed from cart`);
        });

        cartItemsContainer.appendChild(itemElement);
        total += item.price;
    });

    cartTotalElement.textContent = `$${total.toFixed(2)}`;
}

// ===== Checkout Form Handling =====
checkoutForm.addEventListener('submit', function(e) {
    e.preventDefault();

    // Validate form
    const formData = new FormData(checkoutForm);
    let isValid = true;

    for (let [key, value] of formData.entries()) {
        if (!value || value.trim() === '') {
            showToast(`⚠ Please fill in ${key}`, 2000);
            isValid = false;
            break;
        }
    }

    if (!isValid) return;

    // Calculate order total
    const orderTotal = cart.reduce((sum, item) => sum + item.price, 0).toFixed(2);

    // Simulate order processing
    const submitButton = checkoutForm.querySelector('button[type="submit"]');
    const originalText = submitButton.textContent;

    submitButton.disabled = true;
    submitButton.textContent = 'Processing...';

    setTimeout(() => {
        showToast(`🎉 Order placed successfully! Total: $${orderTotal}`, 5000);

        // Reset cart and form
        cart = [];
        updateCartDisplay();
        checkoutForm.reset();
        submitButton.disabled = false;
        submitButton.textContent = originalText;

        // Scroll to products section
        document.getElementById('products').scrollIntoView({ behavior: 'smooth' });
    }, 1500);
});

// ===== Card Details Toggle =====
const paymentRadios = document.querySelectorAll('input[name="payment"]');
paymentRadios.forEach(radio => {
    radio.addEventListener('change', function() {
        if (this.value === 'paypal') {
            cardDetailsSection.style.display = 'none';
        } else {
            cardDetailsSection.style.display = 'block';
        }
    });
});

// ===== Card Number Formatting =====
const cardNumberInput = document.getElementById('cardNumber');
if (cardNumberInput) {
    cardNumberInput.addEventListener('input', function(e) {
        let value = e.target.value.replace(/\s/g, '').replace(/[^0-9]/g, '');

        // Add spaces every 4 digits
        const formatted = value.match(/.{1,4}/g)?.join(' ') || '';
        e.target.value = formatted;
    });
}

// ===== Expiry Date Formatting =====
const expiryInput = document.getElementById('expiry');
if (expiryInput) {
    expiryInput.addEventListener('input', function(e) {
        let value = e.target.value.replace(/\D/g, '');

        if (value.length > 2) {
            value = value.slice(0, 2) + '/' + value.slice(2, 4);
        } else if (value.length === 1) {
            value += '/';
        }

        e.target.value = value;
    });
}

// ===== Smooth Scroll for Navigation =====
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function(e) {
        const targetId = this.getAttribute('href');
        if (targetId === '#') return;

        e.preventDefault();
        const targetElement = document.querySelector(targetId);

        if (targetElement) {
            const navbarHeight = document.querySelector('.navbar').offsetHeight;
            const targetPosition = targetElement.offsetTop - navbarHeight;

            window.scrollTo({
                top: targetPosition,
                behavior: 'smooth'
            });

            // Update active nav link
            document.querySelectorAll('.nav-links a').forEach(link => {
                link.classList.remove('active');
                if (link.getAttribute('href') === targetId) {
                    link.classList.add('active');
                }
            });
        }
    });
});

// ===== Mobile Menu Toggle =====
const mobileMenuBtn = document.querySelector('.mobile-menu-btn');
if (mobileMenuBtn) {
    mobileMenuBtn.addEventListener('click', function() {
        const navLinks = document.querySelector('.nav-links');

        if (navLinks.style.display === 'flex') {
            navLinks.style.display = '';
            this.textContent = '☰';
        } else {
            navLinks.style.display = 'flex';
            navLinks.style.flexDirection = 'column';
            navLinks.style.position = 'absolute';
            navLinks.style.top = '100%';
            navLinks.style.left = '0';
            navLinks.style.right = '0';
            navLinks.style.background = '#fff';
            navLinks.style.padding = '2rem';
            navLinks.style.boxShadow = '0 4px 20px rgba(0,0,0,0.1)';
            this.textContent = '✕';
        }
    });
}

// ===== Intersection Observer for Scroll Animations =====
const observerOptions = {
    threshold: 0.1,
    rootMargin: '0px 0px -50px 0px'
};

const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            entry.target.style.opacity = '1';
            entry.target.style.transform = 'translateY(0)';
        }
    });
}, observerOptions);

// Apply fade-in animation to sections
document.querySelectorAll('.product-card, .about-item, .contact-item').forEach(el => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(20px)';
    el.style.transition = 'all 0.6s ease';
    observer.observe(el);
});

// ===== Console Welcome Message =====
console.log('%c🎨 Printables by Advi', 'font-size: 24px; font-weight: bold; color: #764ba2;');
console.log('Welcome to our digital products website!');
console.log('Features: Add products to cart, checkout with secure payment, contact us for support.');
