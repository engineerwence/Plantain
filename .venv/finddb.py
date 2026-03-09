import mysql.connector
db = mysql.connector.connect(host='localhost', user='root', password='')
cursor = db.cursor()
cursor.execute('SHOW VARIABLES LIKE "datadir"')
print(cursor.fetchone())
db.close()
