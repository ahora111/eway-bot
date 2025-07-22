def fetch_product_links():
    url = "https://naminet.co/list/llp-13/%DA%AF%D9%88%D8%B4%DB%8C-%D8%B3%D8%A7%D9%85%D8%B3%D9%88%D9%86%DA%AF"
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=options)
    driver.get(url)
    time.sleep(5)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    driver.quit()
    links = []
    # فقط تگ‌های <a> با href که با /product/ شروع می‌شود
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if href.startswith("/product/"):
            link = "https://naminet.co" + href
            links.append(link)
    print("تعداد لینک محصولات پیدا شده:", len(links))
    return list(set(links))  # حذف لینک‌های تکراری
