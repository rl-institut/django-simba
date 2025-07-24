/**
 * This is a minimal config.
 *
 * If you need the full config, get it from here:
 * https://unpkg.com/browse/tailwindcss@latest/stubs/defaultConfig.stub.js
 */

module.exports = {
    content: [
        /**
         * HTML. Paths to Django template files that will contain Tailwind CSS classes.
         */

        /*  Templates within theme app (<tailwind_app_name>/templates), e.g. base.html. */
        '../templates/**/*.html',

        /*
         * Main templates directory of the project (BASE_DIR/templates).
         * Adjust the following line to match your project structure.
         */
        '../../templates/**/*.html',

        /*
         * Templates in other django apps (BASE_DIR/<any_app_name>/templates).
         * Adjust the following line to match your project structure.
         */
        '../../**/templates/**/*.html',

        /**
         * JS: If you use Tailwind CSS in JavaScript, uncomment the following lines and make sure
         * patterns match your project structure.
         */
        /* JS 1: Ignore any JavaScript in node_modules folder. */
        // '!../../**/node_modules',
        /* JS 2: Process all JavaScript files in the project. */
        // '../../**/*.js',

        /**
         * Python: If you use Tailwind CSS classes in Python, uncomment the following line
         * and make sure the pattern below matches your project structure.
         */
        // '../../**/*.py'
    ],
    theme: {
      extend: {
        colors: {
          gradient1: '#0CEBEB',
          gradient2: '#20E3B2',
          gradient3: '#29FFC6',
        },
        keyframes: {
          'hero-fade-in-up': {
            '0%': { opacity: '0', transform: 'translateY(20px)' },
            '100%': { opacity: '0.8', transform: 'translateY(0)' },
          },
        },
        animation: {
          'hero-fade-in-up': 'hero-fade-in-up 0.8s ease-out forwards',
          'hero-fade-in-up-delay-1': 'hero-fade-in-up 0.8s ease-out 0.15s forwards',
          'hero-fade-in-up-delay-2': 'hero-fade-in-up 0.8s ease-out 0.3s forwards',
          'hero-fade-in-up-delay-3': 'hero-fade-in-up 0.8s ease-out 0.45s forwards',
        },
      },
    },
    plugins: [
        /**
         * '@tailwindcss/forms' is the forms plugin that provides a minimal styling
         * for forms. If you don't like it or have own styling for forms,
         * comment the line below to disable '@tailwindcss/forms'.
         */
        require('@tailwindcss/forms'),
        require('@tailwindcss/typography'),
        require('@tailwindcss/aspect-ratio'),
    ],
    safelist: [
      'scale-95', 'scale-100',
      'opacity-0', 'opacity-100',
      'pointer-events-none', 'pointer-events-auto'
    ]
}
