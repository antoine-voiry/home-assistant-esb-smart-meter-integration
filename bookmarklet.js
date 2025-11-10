// ESB Networks Cookie Extractor Bookmarklet
// 
// To install this bookmarklet:
// 1. Create a new bookmark in your browser
// 2. Copy the MINIFIED VERSION below as the bookmark URL
// 3. Name it "Copy ESB Cookies"
// 4. After logging in to https://myaccount.esbnetworks.ie, click the bookmark
// 5. Copy the cookies shown in the popup
// 6. Paste them in Home Assistant integration settings

// READABLE VERSION (for understanding the code):
(function() {
    // Get all cookies from the current page
    const cookies = document.cookie;
    
    // Check if any cookies exist
    if (!cookies || cookies.length === 0) {
        alert('No cookies found.\n\nMake sure you are:\n1. Logged in to ESB Networks\n2. On the myaccount.esbnetworks.ie domain');
        return;
    }
    
    // Count cookies
    const cookieCount = cookies.split(';').length;
    
    // Display cookies in a prompt for easy copying
    const copied = prompt(
        'ESB Networks Cookies (' + cookieCount + ' found)\n\n' +
        'Select ALL text below and copy (Ctrl+C or Cmd+C):\n' +
        'Then paste in Home Assistant > ESB Smart Meter > Configure',
        cookies
    );
    
    // If user clicked OK (not Cancel), try to copy to clipboard automatically
    if (copied !== null) {
        try {
            navigator.clipboard.writeText(cookies).then(
                () => alert('✓ Cookies copied to clipboard!\n\nNow paste them in Home Assistant.'),
                () => {} // Silently fail if clipboard API not available
            );
        } catch (e) {
            // Clipboard API not available, that's ok - user already copied manually
        }
    }
})();

// MINIFIED VERSION (use this as your bookmark URL):
// javascript:(function(){const c=document.cookie;if(!c||c.length===0){alert('No cookies found.\n\nMake sure you are:\n1. Logged in to ESB Networks\n2. On the myaccount.esbnetworks.ie domain');return;}const n=c.split(';').length;const r=prompt('ESB Networks Cookies ('+n+' found)\n\nSelect ALL text below and copy (Ctrl+C or Cmd+C):\nThen paste in Home Assistant > ESB Smart Meter > Configure',c);if(r!==null){try{navigator.clipboard.writeText(c).then(()=>alert('✓ Cookies copied to clipboard!\n\nNow paste them in Home Assistant.'),()=>{})}catch(e){}}})();
