class Person:
    def __init__(self, name):
        self.name = name

    def greet(self):
        return f"Hello, my name is {self.name}."

a = Person("Alice")
b = Person("Bob")

print(f"`a.greet` {a.greet}")
print(f"`a.greet()` {a.greet()}")
print(f"`b.greet()` {b.greet()}")
print(a.greet.__name__ is a.greet.__name__)
print(f"`a.greet.__func__` {a.greet.__func__}")
print(f"`a.greet.__name__` {a.greet.__name__}")
print(f"`a.greet` {a.greet}")
print(f"`a.greet` {a.greet}")
