// SSHCO Partners Interactive Features
document.addEventListener('DOMContentLoaded', function() {
    'use strict';
    
    // Configuration
    const config = {
        animationDuration: 300,
        scrollOffset: 100,
        typewriterSpeed: 50,
        fadeInDelay: 100
    };
    
    // Utility functions
    const utils = {
        debounce: function(func, wait) {
            let timeout;
            return function executedFunction(...args) {
                const later = function() {
                    clearTimeout(timeout);
                    func(...args);
                };
                clearTimeout(timeout);
                timeout = setTimeout(later, wait);
            };
        },
        
        throttle: function(func, limit) {
            let inThrottle;
            return function() {
                const args = arguments;
                const context = this;
                if (!inThrottle) {
                    func.apply(context, args);
                    inThrottle = true;
                    setTimeout(() => inThrottle = false, limit);
                }
            };
        },
        
        isElementInViewport: function(el) {
            const rect = el.getBoundingClientRect();
            return (
                rect.top >= 0 &&
                rect.left >= 0 &&
                rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
                rect.right <= (window.innerWidth || document.documentElement.clientWidth)
            );
        },
        
        isElementPartiallyInViewport: function(el) {
            const rect = el.getBoundingClientRect();
            const windowHeight = window.innerHeight || document.documentElement.clientHeight;
            const windowWidth = window.innerWidth || document.documentElement.clientWidth;
            
            return (rect.bottom >= 0 && rect.top <= windowHeight) &&
                   (rect.right >= 0 && rect.left <= windowWidth);
        }
    };
    
    // Mobile menu functionality
    const initMobileMenu = function() {
        const mobileMenuBtn = document.getElementById('mobile-menu-btn');
        const mobileMenu = document.getElementById('mobile-menu');
        
        if (mobileMenuBtn && mobileMenu) {
            mobileMenuBtn.addEventListener('click', function() {
                mobileMenu.classList.toggle('hidden');
                
                // Update button icon
                const icon = mobileMenuBtn.querySelector('i');
                if (mobileMenu.classList.contains('hidden')) {
                    icon.className = 'fas fa-bars text-xl';
                    mobileMenuBtn.setAttribute('aria-expanded', 'false');
                } else {
                    icon.className = 'fas fa-times text-xl';
                    mobileMenuBtn.setAttribute('aria-expanded', 'true');
                }
            });
            
            // Close mobile menu when clicking on links
            const mobileMenuLinks = mobileMenu.querySelectorAll('a');
            mobileMenuLinks.forEach(link => {
                link.addEventListener('click', function() {
                    mobileMenu.classList.add('hidden');
                    const icon = mobileMenuBtn.querySelector('i');
                    icon.className = 'fas fa-bars text-xl';
                    mobileMenuBtn.setAttribute('aria-expanded', 'false');
                });
            });
            
            // Close mobile menu when clicking outside
            document.addEventListener('click', function(event) {
                if (!mobileMenuBtn.contains(event.target) && !mobileMenu.contains(event.target)) {
                    mobileMenu.classList.add('hidden');
                    const icon = mobileMenuBtn.querySelector('i');
                    icon.className = 'fas fa-bars text-xl';
                    mobileMenuBtn.setAttribute('aria-expanded', 'false');
                }
            });
        }
    };
    
    // Smooth scrolling for navigation links
    const initSmoothScrolling = function() {
        const navLinks = document.querySelectorAll('a[href^="#"]');
        
        navLinks.forEach(link => {
            link.addEventListener('click', function(e) {
                e.preventDefault();
                
                const targetId = this.getAttribute('href').substring(1);
                const targetElement = document.getElementById(targetId);
                
                if (targetElement) {
                    const headerOffset = config.scrollOffset;
                    const elementPosition = targetElement.getBoundingClientRect().top;
                    const offsetPosition = elementPosition + window.pageYOffset - headerOffset;
                    
                    window.scrollTo({
                        top: offsetPosition,
                        behavior: 'smooth'
                    });
                }
            });
        });
    };
    
    // Header scroll effects
    const initHeaderScrollEffects = function() {
        const header = document.querySelector('header');
        if (!header) return;
        
        const handleScroll = utils.throttle(function() {
            if (window.scrollY > 50) {
                header.classList.add('shadow-lg', 'bg-opacity-95');
            } else {
                header.classList.remove('shadow-lg', 'bg-opacity-95');
            }
        }, 16);
        
        window.addEventListener('scroll', handleScroll);
    };
    
    // Animated statistics counter
    const initStatsAnimation = function() {
        const statNumbers = document.querySelectorAll('.stat-number');
        let hasAnimated = false;
        
        const animateStats = function() {
            if (hasAnimated) return;
            
            statNumbers.forEach(stat => {
                if (utils.isElementPartiallyInViewport(stat)) {
                    hasAnimated = true;
                    const finalValue = stat.textContent;
                    const isNumber = !isNaN(parseInt(finalValue));
                    
                    if (isNumber) {
                        const duration = 2000;
                        const startTime = performance.now();
                        const finalNum = parseInt(finalValue);
                        
                        const updateNumber = (currentTime) => {
                            const elapsed = currentTime - startTime;
                            const progress = Math.min(elapsed / duration, 1);
                            const easeOutQuart = 1 - Math.pow(1 - progress, 4);
                            const currentNum = Math.floor(finalNum * easeOutQuart);
                            
                            stat.textContent = currentNum;
                            
                            if (progress < 1) {
                                requestAnimationFrame(updateNumber);
                            } else {
                                stat.textContent = finalValue;
                            }
                        };
                        
                        requestAnimationFrame(updateNumber);
                    }
                }
            });
        };
        
        const handleStatsScroll = utils.throttle(animateStats, 100);
        window.addEventListener('scroll', handleStatsScroll);
        animateStats(); // Check on load
    };
    
    // Intersection Observer for fade-in animations
    const initFadeInAnimations = function() {
        const fadeElements = document.querySelectorAll('.solution-card, .impact-card, section > div > h2');
        
        if ('IntersectionObserver' in window) {
            const fadeObserver = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        entry.target.style.opacity = '1';
                        entry.target.style.transform = 'translateY(0)';
                        fadeObserver.unobserve(entry.target);
                    }
                });
            }, {
                threshold: 0.1,
                rootMargin: '0px 0px -50px 0px'
            });
            
            fadeElements.forEach((element, index) => {
                element.style.opacity = '0';
                element.style.transform = 'translateY(30px)';
                element.style.transition = `opacity 0.8s ease ${index * 0.1}s, transform 0.8s ease ${index * 0.1}s`;
                fadeObserver.observe(element);
            });
        }
    };
    
    // Form handling and validation
    const initFormHandling = function() {
        const contactForm = document.getElementById('contact-form');
        if (!contactForm) return;
        
        const formFields = contactForm.querySelectorAll('input, select, textarea');
        const submitButton = contactForm.querySelector('button[type="submit"]');
        
        // Real-time validation
        formFields.forEach(field => {
            field.addEventListener('blur', function() {
                validateField(this);
            });
            
            field.addEventListener('input', function() {
                if (this.classList.contains('form-error')) {
                    validateField(this);
                }
            });
        });
        
        function validateField(field) {
            const value = field.value.trim();
            let isValid = true;
            let errorMessage = '';
            
            // Required field validation
            if (field.hasAttribute('required') && !value) {
                isValid = false;
                errorMessage = 'This field is required';
            }
            
            // Email validation
            if (field.type === 'email' && value) {
                const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
                if (!emailRegex.test(value)) {
                    isValid = false;
                    errorMessage = 'Please enter a valid email address';
                }
            }
            
            // Phone validation
            if (field.type === 'tel' && value) {
                const phoneRegex = /^[\+]?[1-9][\d]{0,15}$/;
                const cleanPhone = value.replace(/[\s\-\(\)]/g, '');
                if (!phoneRegex.test(cleanPhone)) {
                    isValid = false;
                    errorMessage = 'Please enter a valid phone number';
                }
            }
            
            // Update field appearance
            if (isValid) {
                field.classList.remove('form-error');
                field.classList.add('form-success');
                removeErrorMessage(field);
            } else {
                field.classList.remove('form-success');
                field.classList.add('form-error');
                showErrorMessage(field, errorMessage);
            }
            
            return isValid;
        }
        
        function showErrorMessage(field, message) {
            removeErrorMessage(field);
            const errorDiv = document.createElement('div');
            errorDiv.className = 'error-message text-red-500 text-sm mt-1';
            errorDiv.textContent = message;
            field.parentNode.appendChild(errorDiv);
        }
        
        function removeErrorMessage(field) {
            const existingError = field.parentNode.querySelector('.error-message');
            if (existingError) {
                existingError.remove();
            }
        }
        
        // Form submission
        contactForm.addEventListener('submit', function(e) {
            e.preventDefault();
            
            // Validate all fields
            let isFormValid = true;
            formFields.forEach(field => {
                if (!validateField(field)) {
                    isFormValid = false;
                }
            });
            
            if (isFormValid) {
                // Simulate form submission
                submitButton.classList.add('loading');
                submitButton.disabled = true;
                
                // Simulate API call
                setTimeout(() => {
                    submitButton.classList.remove('loading');
                    submitButton.disabled = false;
                    
                    // Show success message
                    showSuccessMessage();
                    contactForm.reset();
                    
                    // Remove validation classes
                    formFields.forEach(field => {
                        field.classList.remove('form-error', 'form-success');
                        removeErrorMessage(field);
                    });
                }, 2000);
            } else {
                // Focus on first error field
                const firstError = contactForm.querySelector('.form-error');
                if (firstError) {
                    firstError.focus();
                }
            }
        });
        
        function showSuccessMessage() {
            const successDiv = document.createElement('div');
            successDiv.className = 'fixed top-4 right-4 bg-green-500 text-white p-4 rounded-lg shadow-lg z-50 transform translate-x-full opacity-0 transition-all duration-300';
            successDiv.innerHTML = `
                <div class="flex items-center space-x-2">
                    <i class="fas fa-check-circle"></i>
                    <span>Thank you! Your message has been sent successfully.</span>
                </div>
            `;
            
            document.body.appendChild(successDiv);
            
            // Animate in
            setTimeout(() => {
                successDiv.classList.remove('translate-x-full', 'opacity-0');
            }, 100);
            
            // Animate out and remove
            setTimeout(() => {
                successDiv.classList.add('translate-x-full', 'opacity-0');
                setTimeout(() => {
                    document.body.removeChild(successDiv);
                }, 300);
            }, 4000);
        }
    };
    
    // Keyboard navigation enhancement
    const initKeyboardNavigation = function() {
        // Skip to main content link
        const skipLink = document.createElement('a');
        skipLink.href = '#solutions';
        skipLink.className = 'sr-only focus:not-sr-only focus:absolute focus:top-4 focus:left-4 bg-primary text-white px-4 py-2 rounded z-50';
        skipLink.textContent = 'Skip to main content';
        document.body.insertBefore(skipLink, document.body.firstChild);
        
        // Enhanced focus management
        document.addEventListener('keydown', function(e) {
            // Escape key closes mobile menu
            if (e.key === 'Escape') {
                const mobileMenu = document.getElementById('mobile-menu');
                if (mobileMenu && !mobileMenu.classList.contains('hidden')) {
                    mobileMenu.classList.add('hidden');
                    document.getElementById('mobile-menu-btn').focus();
                }
            }
        });
    };
    
    // Logo star animation on hover
    const initLogoAnimations = function() {
        const logoStars = document.querySelectorAll('.logo-star, header i.fa-star');
        
        logoStars.forEach((star, index) => {
            star.addEventListener('mouseenter', function() {
                // Stagger animation for multiple stars
                setTimeout(() => {
                    this.style.transform = 'rotate(180deg) scale(1.1)';
                }, index * 50);
            });
            
            star.addEventListener('mouseleave', function() {
                this.style.transform = 'rotate(0deg) scale(1)';
            });
        });
    };
    
    // Scroll progress indicator
    const initScrollProgress = function() {
        const progressBar = document.createElement('div');
        progressBar.className = 'fixed top-0 left-0 h-1 bg-gradient-to-r from-primary via-secondary to-accent z-50 transition-all duration-150 ease-out';
        progressBar.style.width = '0%';
        document.body.appendChild(progressBar);
        
        const updateProgress = utils.throttle(function() {
            const winScroll = document.body.scrollTop || document.documentElement.scrollTop;
            const height = document.documentElement.scrollHeight - document.documentElement.clientHeight;
            const scrolled = (winScroll / height) * 100;
            progressBar.style.width = scrolled + '%';
        }, 16);
        
        window.addEventListener('scroll', updateProgress);
    };
    
    // Performance monitoring
    const initPerformanceMonitoring = function() {
        if ('performance' in window) {
            window.addEventListener('load', function() {
                setTimeout(() => {
                    const perfData = performance.getEntriesByType('navigation')[0];
                    console.log('SSHCO Partners - Page Load Performance:', {
                        loadTime: perfData.loadEventEnd - perfData.loadEventStart,
                        domContentLoaded: perfData.domContentLoadedEventEnd - perfData.domContentLoadedEventStart,
                        totalTime: perfData.loadEventEnd - perfData.fetchStart
                    });
                }, 0);
            });
        }
    };
    
    // Accessibility improvements
    const initAccessibilityEnhancements = function() {
        // Add role attributes where needed
        const nav = document.querySelector('nav');
        if (nav && !nav.getAttribute('role')) {
            nav.setAttribute('role', 'navigation');
        }
        
        // Add aria-labels to buttons without text
        const iconButtons = document.querySelectorAll('button i.fa, a i.fa');
        iconButtons.forEach(icon => {
            const button = icon.closest('button, a');
            if (button && !button.getAttribute('aria-label') && !button.textContent.trim()) {
                const iconClass = icon.className;
                if (iconClass.includes('fa-bars')) {
                    button.setAttribute('aria-label', 'Open navigation menu');
                } else if (iconClass.includes('fa-times')) {
                    button.setAttribute('aria-label', 'Close navigation menu');
                }
            }
        });
        
        // Enhance form accessibility
        const formLabels = document.querySelectorAll('label');
        formLabels.forEach(label => {
            const input = label.parentNode.querySelector('input, select, textarea');
            if (input && !input.getAttribute('id')) {
                const id = 'field-' + Math.random().toString(36).substr(2, 9);
                input.setAttribute('id', id);
                label.setAttribute('for', id);
            }
        });
    };
    
    // Error handling
    const initErrorHandling = function() {
        window.addEventListener('error', function(e) {
            console.error('SSHCO Partners - JavaScript Error:', e.error);
            
            // Show user-friendly error message for critical failures
            if (e.error && e.error.message && e.error.message.includes('critical')) {
                const errorDiv = document.createElement('div');
                errorDiv.className = 'fixed top-4 right-4 bg-red-500 text-white p-4 rounded-lg shadow-lg z-50';
                errorDiv.innerHTML = `
                    <div class="flex items-center space-x-2">
                        <i class="fas fa-exclamation-triangle"></i>
                        <span>We're experiencing technical difficulties. Please refresh the page.</span>
                    </div>
                `;
                document.body.appendChild(errorDiv);
                
                setTimeout(() => {
                    document.body.removeChild(errorDiv);
                }, 5000);
            }
        });
    };
    
    // Initialize all features
    const init = function() {
        try {
            initMobileMenu();
            initSmoothScrolling();
            initHeaderScrollEffects();
            initStatsAnimation();
            initFadeInAnimations();
            initFormHandling();
            initKeyboardNavigation();
            initLogoAnimations();
            initScrollProgress();
            initPerformanceMonitoring();
            initAccessibilityEnhancements();
            initErrorHandling();
            
            console.log('SSHCO Partners - All interactive features initialized successfully');
        } catch (error) {
            console.error('SSHCO Partners - Initialization error:', error);
        }
    };
    
    // Run initialization
    init();
    
    // Expose utility functions globally for extensibility
    window.SSHCOUtils = utils;
});

// Service Worker registration for PWA capabilities
if ('serviceWorker' in navigator) {
    window.addEventListener('load', function() {
        navigator.serviceWorker.register('/sw.js')
            .then(function(registration) {
                console.log('SSHCO Partners - Service Worker registered successfully');
            })
            .catch(function(error) {
                console.log('SSHCO Partners - Service Worker registration failed');
            });
    });
}