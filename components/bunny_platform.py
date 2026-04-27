class Platform:
    def __init__(self, new_vertex, new_size):
        self.vertex = new_vertex
        self.size = new_size
    
    def get_left_x(self):
        return self.vertex.x
    
    def get_right_x(self):
        return self.vertex.x + self.size.x

    def get_bottom_y(self):
        return self.vertex.y + self.size.y

    def get_top_y(self):
        return self.vertex.y